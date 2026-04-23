"""Economic layer: cash flow, payroll, fixed costs, inventory, shocks."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np

from .config import STAFF, MENU_ITEMS
from .game_state import (
    RENT_PER_MONTH, UTILITIES_PER_DAY, INSURANCE_PER_DAY,
    POS_SUBSCRIPTION_PER_DAY, LOYALTY_COST_PER_DAY,
    PERISHABLE_CATEGORIES, PERISHABLE_SPOILAGE_RATE,
    current_date, date_for,
)


def _default_staff_count_for_day(dow: int) -> dict[str, int]:
    """Count STAFF members scheduled on this weekday by role."""
    counts: dict[str, int] = {}
    for s in STAFF:
        for (d, _, _) in s.shift_pattern:
            if d == dow:
                counts[s.role] = counts.get(s.role, 0) + 1
                break
    return counts


def scheduled_staff_for_date(state: dict, date: str) -> dict[str, int]:
    """Effective staff count by role for a given date (default ∪ override)."""
    dow = datetime.fromisoformat(date).weekday()
    default = _default_staff_count_for_day(dow)
    override = state["staffing_override"].get(date, {})
    out = dict(default)
    out.update(override)
    return out


def payroll_for_date(state: dict, date: str) -> float:
    """Compute EUR payroll for the day given scheduled roles (avg wage × avg hours)."""
    dow = datetime.fromisoformat(date).weekday()
    by_role: dict[str, list] = {}
    for s in STAFF:
        by_role.setdefault(s.role, []).append(s)
    effective = scheduled_staff_for_date(state, date)
    total = 0.0
    for role, n in effective.items():
        if role not in by_role or n <= 0:
            continue
        wages = [s.hourly_wage_eur for s in by_role[role]]
        # typical shift length for this role on this dow (fall back to 8h)
        shift_hours = []
        for s in by_role[role]:
            for (d, st, en) in s.shift_pattern:
                if d == dow:
                    shift_hours.append(en - st)
        avg_wage = float(np.mean(wages))
        avg_hours = float(np.mean(shift_hours)) if shift_hours else 8.0
        total += n * avg_wage * avg_hours
    return round(total, 2)


def staffing_pressure(state: dict, date: str) -> float:
    """Ratio of effective staffing to default; <1 = understaffed, >1 = overstaffed.
    Used as a multiplier inside the day sim to modulate efficiency/wait."""
    dow = datetime.fromisoformat(date).weekday()
    default = _default_staff_count_for_day(dow)
    effective = scheduled_staff_for_date(state, date)
    # Weight kitchen + server roles most heavily
    weights = {"line_cook": 2.0, "sous_chef": 1.5, "chef": 1.0,
               "server": 2.0, "host": 1.0, "bartender": 1.0,
               "busser": 0.5, "dishwasher": 0.5, "manager": 0.3}
    num = den = 0.0
    for role, w in weights.items():
        d = default.get(role, 0)
        e = effective.get(role, d)
        if d == 0:
            continue
        num += e * w
        den += d * w
    return (num / den) if den > 0 else 1.0


def fixed_costs_for_date(state: dict, date: str) -> dict[str, float]:
    """Daily + monthly-spread fixed costs charged on this date."""
    d = datetime.fromisoformat(date).date()
    # Charge rent on the 1st of the month (or on day_index 0 if month-start missed)
    rent = RENT_PER_MONTH if d.day == 1 else 0.0
    return {
        "rent": rent,
        "utilities": UTILITIES_PER_DAY,
        "insurance": INSURANCE_PER_DAY,
        "pos": POS_SUBSCRIPTION_PER_DAY,
        "loyalty_program": LOYALTY_COST_PER_DAY if state["loyalty_enabled"] else 0.0,
    }


def apply_spoilage(state: dict) -> dict[str, int]:
    """Decay perishable inventory. Returns {item_id: units_spoiled}."""
    cat_by_id = {row[0]: row[2] for row in MENU_ITEMS}
    spoiled: dict[str, int] = {}
    for item_id, qty in list(state["inventory"].items()):
        cat = cat_by_id.get(item_id)
        if cat in PERISHABLE_CATEGORIES and qty > 0:
            loss = int(round(qty * PERISHABLE_SPOILAGE_RATE))
            if loss > 0:
                state["inventory"][item_id] = qty - loss
                spoiled[item_id] = loss
    return spoiled


def apply_ingredient_shock(state: dict, rng: np.random.Generator) -> float | None:
    """Random walk on global ingredient price multiplier. Occasional shocks."""
    # Tiny daily drift
    state["ingredient_price_multiplier"] *= float(np.exp(rng.normal(0, 0.01)))
    # 5% chance per day of a notable shock (±10–25%)
    shock = None
    if rng.random() < 0.05:
        shock = float(rng.choice([-0.20, -0.15, -0.10, 0.10, 0.15, 0.20, 0.25]))
        state["ingredient_price_multiplier"] *= (1.0 + shock)
    # Clamp to sane range
    state["ingredient_price_multiplier"] = float(
        np.clip(state["ingredient_price_multiplier"], 0.6, 2.0)
    )
    return shock


def restock_cost(state: dict, item_id: str, qty: int) -> float:
    """Cost to restock: unit food cost × qty × 1.15 (supplier markup)."""
    # Find food cost (apply current ingredient multiplier)
    for row in MENU_ITEMS:
        if row[0] == item_id:
            base_cost = row[4]
            return round(base_cost * state["ingredient_price_multiplier"] * qty * 1.15, 2)
    for a in state["menu_added"]:
        if a["id"] == item_id:
            return round(float(a["cost"]) * state["ingredient_price_multiplier"] * qty * 1.15, 2)
    return 0.0


def marketing_lift(spend_eur: float) -> float:
    """Arrival rate multiplier from marketing spend with diminishing returns."""
    if spend_eur <= 0:
        return 1.0
    return 1.0 + 0.5 * (1.0 - np.exp(-spend_eur / 400.0))


def reputation_multiplier(reputation: float) -> float:
    """Arrival rate multiplier from current reputation (anchored at 4.0)."""
    return float(max(0.6, min(1.4, 1.0 + 0.25 * (reputation - 4.0))))


def update_reputation(state: dict, mean_satisfaction: float) -> None:
    """Slow EWMA toward a satisfaction-implied score (mean 0.5 → 3.0 stars,
    mean 0.9 → 4.8 stars)."""
    implied = 2.0 + 3.0 * float(mean_satisfaction)  # roughly 2.0–5.0
    state["satisfaction_ewma"] = 0.85 * state["satisfaction_ewma"] + 0.15 * mean_satisfaction
    state["reputation"] = round(0.90 * state["reputation"] + 0.10 * implied, 3)


def sample_weather(rng: np.random.Generator, dow: int) -> dict:
    """Weather with seasonal-ish bias. Returns {condition, arrival_multiplier}."""
    conditions = ["sunny", "cloudy", "rain", "storm"]
    # Slightly better weekends to match Rotterdam spring
    base_probs = [0.30, 0.45, 0.20, 0.05]
    if dow in (5, 6):
        base_probs = [0.35, 0.45, 0.17, 0.03]
    c = rng.choice(conditions, p=base_probs).item()
    mult = {"sunny": 1.08, "cloudy": 1.0, "rain": 0.85, "storm": 0.65}[c]
    return {"condition": c, "mult": mult}


def roll_incident(rng: np.random.Generator, date: str) -> dict | None:
    """Low-probability daily incidents. Return event dict or None."""
    if rng.random() > 0.08:
        return None
    kind = rng.choice([
        "oven_breakdown", "staff_sick", "bad_review", "noise_complaint",
        "health_inspection", "power_flicker",
    ]).item()
    effects = {
        "oven_breakdown":    {"kitchen_efficiency": 0.6, "cost": 350.0},
        "staff_sick":         {"staff_efficiency": 0.85, "cost": 0.0},
        "bad_review":         {"reputation_delta": -0.10, "cost": 0.0},
        "noise_complaint":    {"reputation_delta": -0.05, "cost": 150.0},
        "health_inspection":  {"reputation_delta": 0.05, "cost": 0.0},
        "power_flicker":      {"kitchen_efficiency": 0.85, "cost": 80.0},
    }[kind]
    return {"date": date, "kind": kind, **effects}
