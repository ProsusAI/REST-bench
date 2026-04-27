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
from .tuning import (
    COMPETITOR, INCIDENT, INVENTORY, MARKETING, REPUTATION,
    SHOCK, STAFF as STAFF_CFG, WEATHER,
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
    weights = STAFF_CFG.PRESSURE_WEIGHTS
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
    """Daily drift + scheduled shocks (Phase 2.4).

    Shocks are no longer surprise-rolled per day; they are scheduled by
    earlier `roll_supplier_news` calls 1-3 days in advance. This day-sim
    step:
      1. Applies a small daily log-normal drift (unchanged).
      2. Fires any scheduled shock whose date matches today.
      3. Clamps the multiplier into [0.6, 2.0].
    """
    from .game_state import current_date
    today = current_date(state)

    state["ingredient_price_multiplier"] *= float(
        np.exp(rng.normal(0, SHOCK.DAILY_DRIFT_SIGMA))
    )

    shock = None
    news = state.get("supplier_news", [])
    remaining = []
    for item in news:
        if item["fire_on_date"] == today:
            # Realized magnitude has noise around the predicted center.
            mag = float(item["predicted_direction"]) * float(np.abs(
                rng.normal(item["predicted_magnitude_center"],
                           item["predicted_magnitude_sigma"])
            ))
            state["ingredient_price_multiplier"] *= (1.0 + mag)
            shock = mag
            item["realized_shock"] = round(mag, 4)
            state.setdefault("supplier_news_history", []).append(item)
        elif item["fire_on_date"] > today:
            remaining.append(item)
        # past-due (we somehow skipped it) gets archived without firing
    state["supplier_news"] = remaining

    state["ingredient_price_multiplier"] = float(np.clip(
        state["ingredient_price_multiplier"],
        SHOCK.PRICE_MULT_MIN, SHOCK.PRICE_MULT_MAX,
    ))
    return shock


def roll_supplier_news(state: dict, rng: np.random.Generator) -> dict | None:
    """Phase 2.4: probabilistically queue a 1-3-day-ahead shock notice."""
    from datetime import datetime, timedelta
    from .game_state import current_date
    from .tuning import SUPPLIER_NEWS

    if rng.random() >= SUPPLIER_NEWS.DAILY_NEWS_PROBABILITY:
        return None
    today = current_date(state)
    lead = int(rng.choice(SUPPLIER_NEWS.LEAD_TIMES_DAYS))
    fire_date = (datetime.fromisoformat(today)
                 + timedelta(days=lead)).date().isoformat()
    direction = -1 if rng.random() < 0.45 else +1   # slight upward bias
    center = float(rng.choice([0.10, 0.15, 0.20, 0.25]))
    sigma = float(rng.uniform(0.02, 0.06))
    confidence = float(rng.uniform(0.65, 0.92))
    item = {
        "posted_on": today,
        "fire_on_date": fire_date,
        "lead_days": lead,
        "predicted_direction": direction,
        "predicted_magnitude_center": center,
        "predicted_magnitude_sigma": sigma,
        "predicted_magnitude_range": [
            round(center - 2*sigma, 3), round(center + 2*sigma, 3),
        ],
        "confidence": round(confidence, 2),
        "headline": _format_news_headline(direction, center, lead, confidence),
    }
    state.setdefault("supplier_news", []).append(item)
    return item


def _format_news_headline(direction: int, center: float, lead: int, conf: float) -> str:
    sign = "up" if direction > 0 else "down"
    return (f"Supplier alert: ingredient prices likely {sign} "
            f"~{int(center*100)}% in {lead} day{'s' if lead != 1 else ''} "
            f"(confidence {conf:.0%})")


def restock_cost(state: dict, item_id: str, qty: int) -> float:
    """Cost to restock: unit food cost × qty × supplier markup."""
    mult = state["ingredient_price_multiplier"] * INVENTORY.SUPPLIER_MARKUP
    for row in MENU_ITEMS:
        if row[0] == item_id:
            return round(row[4] * mult * qty, 2)
    for a in state["menu_added"]:
        if a["id"] == item_id:
            return round(float(a["cost"]) * mult * qty, 2)
    return 0.0


def marketing_lift(spend_eur: float) -> float:
    """Arrival rate multiplier from marketing spend with diminishing returns."""
    if spend_eur <= 0:
        return 1.0
    return 1.0 + MARKETING.MAX_LIFT * (
        1.0 - np.exp(-spend_eur / MARKETING.DIMINISHING_RETURNS_HALFLIFE_EUR)
    )


def reputation_multiplier(reputation: float) -> float:
    """Arrival rate multiplier from current reputation (anchored at 4.0)."""
    raw = 1.0 + REPUTATION.MULT_PER_STAR * (reputation - REPUTATION.ANCHOR)
    return float(max(REPUTATION.MULT_FLOOR, min(REPUTATION.MULT_CEIL, raw)))


def update_reputation(state: dict, mean_satisfaction: float) -> None:
    """Slow EWMA toward a satisfaction-implied star score."""
    implied = REPUTATION.IMPLIED_INTERCEPT + REPUTATION.IMPLIED_SLOPE * float(mean_satisfaction)
    state["satisfaction_ewma"] = (
        REPUTATION.EWMA_SATISFACTION_RETAIN * state["satisfaction_ewma"]
        + (1.0 - REPUTATION.EWMA_SATISFACTION_RETAIN) * mean_satisfaction
    )
    state["reputation"] = round(
        REPUTATION.EWMA_REPUTATION_RETAIN * state["reputation"]
        + (1.0 - REPUTATION.EWMA_REPUTATION_RETAIN) * implied,
        3,
    )


def sample_weather(rng: np.random.Generator, dow: int) -> dict:
    """Weather with weekend bias. Returns {condition, mult}."""
    probs = WEATHER.BASE_PROBS["weekend" if dow in (5, 6) else "weekday"]
    c = rng.choice(list(WEATHER.CONDITIONS), p=probs).item()
    return {"condition": c, "mult": WEATHER.MULTIPLIERS[c]}


def roll_incident(rng: np.random.Generator, date: str) -> dict | None:
    """Low-probability daily incidents. Return event dict or None."""
    if rng.random() > INCIDENT.DAILY_PROBABILITY:
        return None
    kind = rng.choice(list(INCIDENT.KINDS)).item()
    return {"date": date, "kind": kind, **INCIDENT.EFFECTS[kind]}
