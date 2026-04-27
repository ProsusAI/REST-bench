"""Persistent game state for the restaurant management simulation.

Single JSON file on disk. All mutations go through load/save.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any

from .config import MENU_ITEMS, STAFF, TABLES


def _game_dir() -> str:
    return os.environ.get(
        "REST_GAME_DIR",
        os.path.join(os.path.dirname(__file__), "..", "game"),
    )


def _state_path() -> str:
    return os.path.join(_game_dir(), "state.json")


def _reservations_path() -> str:
    return os.path.join(_game_dir(), "reservations.json")


GAME_DIR = _game_dir()
STATE_PATH = _state_path()


from .tuning import COHORTS, FIXED_COSTS, INVENTORY, REPUTATION, SATISFACTION

# Backward-compat aliases for external callers.
RENT_PER_MONTH = FIXED_COSTS.RENT_PER_MONTH_EUR
UTILITIES_PER_DAY = FIXED_COSTS.UTILITIES_PER_DAY_EUR
INSURANCE_PER_DAY = FIXED_COSTS.INSURANCE_PER_DAY_EUR
POS_SUBSCRIPTION_PER_DAY = FIXED_COSTS.POS_PER_DAY_EUR
LOYALTY_COST_PER_DAY = FIXED_COSTS.LOYALTY_PER_DAY_EUR
DEFAULT_STOCK = INVENTORY.DEFAULT_STOCK_PER_ITEM
PERISHABLE_CATEGORIES = INVENTORY.PERISHABLE_CATEGORIES
PERISHABLE_SPOILAGE_RATE = INVENTORY.SPOILAGE_RATE_PER_DAY


def default_state(seed: int, start_date: str, total_days: int,
                  starting_cash: float) -> dict:
    menu_ids = [row[0] for row in MENU_ITEMS]
    return {
        "seed": seed,
        "start_date": start_date,
        "total_days": total_days,
        "day_index": 0,                 # next day to advance
        "cash_eur": starting_cash,
        "starting_cash_eur": starting_cash,
        "reputation": REPUTATION.INITIAL,
        "satisfaction_ewma": SATISFACTION.INITIAL_EWMA,
        "competitor_active": False,
        "competitor_active_from": None,
        "price_overrides": {},
        "menu_removed": [],
        "menu_added": [],               # list of {id, name, category, price, cost}
        "inventory": {mid: DEFAULT_STOCK for mid in menu_ids},
        "ingredient_price_multiplier": 1.0,
        "staffing_override": {},        # date -> role -> count
        "staff_status": {s.id: {"fatigue": 0.0, "active": True}
                          for s in STAFF},
        "reservation_caps": {},         # date -> int
        "active_promos": [],            # list of {category, discount_pct, until}
        "marketing_spend_by_day": {},
        "loyalty_enabled": False,
        "table_layout": {t.id: t.seats for t in TABLES},
        "table_layout_changes": [],
        "weather_log": {},              # date -> {condition, mult}
        "incidents_log": [],
        "daily_kpis_history": [],
        "pnl_history": [],
        "decisions_log": [],
        "reservations_all": [],         # pre-generated for all days
        "bankrupt": False,
        "bankrupt_on_date": None,
        # Phase 2.1 — customer cohort populations (slow-moving)
        "cohorts": dict(COHORTS.INITIAL),
        # Phase 2.2 — pending review queue (visit_sat, post_on_day_index)
        "review_queue": [],
        # Phase 2.4 — supplier news ahead of ingredient shocks
        "supplier_news": [],
        # Phase 3.1 — happy-hour windows
        "happy_hours": [],  # list of {start_hour, end_hour, discount_pct, categories: [...], from_date, until_date}
    }


def exists() -> bool:
    return os.path.exists(_state_path())


def load() -> dict:
    path = _state_path()
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"No game state at {path}. Run `python -m rest_sim init` first."
        )
    with open(path) as f:
        state = json.load(f)
    # If reservations were stored in a sidecar (Phase 0.4), splice them back
    # in for the engine. The sidecar is the source of truth; state.json never
    # contains the full reservation roster.
    res_path = _reservations_path()
    if os.path.exists(res_path):
        with open(res_path) as f:
            state["reservations_all"] = json.load(f)
    return state


def save(state: dict) -> None:
    os.makedirs(_game_dir(), exist_ok=True)
    # Pull reservations out into a sidecar so they aren't visible in state.json.
    # The agent must use `reservations` / `reservations next` CLI subcommands.
    reservations = state.pop("reservations_all", None)
    try:
        with open(_state_path(), "w") as f:
            json.dump(state, f, indent=2, default=str)
        if reservations is not None:
            with open(_reservations_path(), "w") as f:
                json.dump(reservations, f, default=str)
    finally:
        if reservations is not None:
            state["reservations_all"] = reservations


def date_for(state: dict, day_index: int) -> str:
    d = datetime.fromisoformat(state["start_date"]).date() + timedelta(days=day_index)
    return d.isoformat()


def current_date(state: dict) -> str:
    return date_for(state, state["day_index"])


def is_done(state: dict) -> bool:
    return state["day_index"] >= state["total_days"]


def _decisions_log_path() -> str:
    return os.path.join(_game_dir(), "decisions_log.jsonl")


def _state_digest(state: dict) -> dict:
    """Compact snapshot of state captured alongside each decision.

    Used by post-hoc attribution to associate a decision with the
    pre-day conditions under which it was made.
    """
    return {
        "cash_eur": round(state.get("cash_eur", 0.0), 2),
        "reputation": state.get("reputation"),
        "satisfaction_ewma": round(state.get("satisfaction_ewma", 0.0), 4),
        "ingredient_price_multiplier": round(
            state.get("ingredient_price_multiplier", 1.0), 4
        ),
        "loyalty_enabled": state.get("loyalty_enabled", False),
        "competitor_active": state.get("competitor_active", False),
        "inventory_total": int(sum(state.get("inventory", {}).values())),
    }


def _append_jsonl(path: str, record: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def log_decision(state: dict, action: str, detail: dict) -> None:
    from datetime import datetime as _dt
    rec = {
        "kind": "decision",
        "ts": _dt.utcnow().isoformat(timespec="seconds") + "Z",
        "day_index": state["day_index"],
        "date": current_date(state) if not is_done(state) else None,
        "action": action,
        "detail": detail,
        "pre_state_digest": _state_digest(state),
    }
    state["decisions_log"].append({
        "day_index": rec["day_index"],
        "date": rec["date"],
        "action": action,
        "detail": detail,
    })
    _append_jsonl(_decisions_log_path(), rec)


def log_day_advance(state: dict, kpi: dict) -> None:
    """Append a structured day-advance record after run_one_day completes.
    Joining decisions to outcomes: filter the JSONL by day_index."""
    from datetime import datetime as _dt
    _append_jsonl(_decisions_log_path(), {
        "kind": "day_advance",
        "ts": _dt.utcnow().isoformat(timespec="seconds") + "Z",
        "day_index": kpi["day_index"],
        "date": kpi["date"],
        "kpi": {k: v for k, v in kpi.items() if k != "hour_heat"},
    })


def effective_menu(state: dict) -> list[dict]:
    """Menu dicts after applying price overrides, removals, additions, and
    the current ingredient price multiplier on food cost."""
    from .config import build_menu
    menu = build_menu()
    removed = set(state["menu_removed"])
    out = []
    mult = state["ingredient_price_multiplier"]
    for m in menu:
        if m["id"] in removed:
            continue
        m = dict(m)
        if m["id"] in state["price_overrides"]:
            m["price_eur"] = float(state["price_overrides"][m["id"]])
        m["food_cost_eur"] = round(m["food_cost_eur"] * mult, 3)
        m["margin_eur"] = round(m["price_eur"] - m["food_cost_eur"], 2)
        m["margin_pct"] = round(m["margin_eur"] / m["price_eur"], 3) if m["price_eur"] > 0 else 0
        out.append(m)
    # Re-rank popularity within category (Zipf on surviving items)
    from .distributions import zipf_weights
    by_cat: dict[str, list] = {}
    for m in out:
        by_cat.setdefault(m["category"], []).append(m)
    for cat, items in by_cat.items():
        items.sort(key=lambda x: -x["popularity_weight"])  # preserve existing order
        w = zipf_weights(len(items))
        for i, it in enumerate(items):
            it["popularity_weight"] = float(w[i])
    # Append agent-added items
    for a in state["menu_added"]:
        cat = a["category"]
        cat_items = by_cat.setdefault(cat, [])
        cat_items.append({
            "id": a["id"],
            "name": a["name"],
            "category": cat,
            "price_eur": float(a["price"]),
            "food_cost_eur": round(float(a["cost"]) * mult, 3),
            "popularity_weight": 0.05,  # new items start niche
            "margin_eur": round(float(a["price"]) - float(a["cost"]) * mult, 2),
            "margin_pct": round((float(a["price"]) - float(a["cost"]) * mult) / float(a["price"]), 3),
        })
    # Flatten
    flat = []
    for items in by_cat.values():
        flat.extend(items)
    return flat


def active_promo_discount(state: dict, category: str, date: str) -> float:
    """Return active discount pct for a category on a given date (0 if none)."""
    total = 0.0
    for p in state["active_promos"]:
        if p["category"] == category and date <= p["until"]:
            total = max(total, float(p["discount_pct"]))
    return total


def active_happy_hour_discount(state: dict, category: str, date: str,
                                arrival_minute: float) -> float:
    """Phase 3.1: discount from any active happy-hour window.

    A guest who arrives within [start_hour, end_hour] on a covered date
    sees the configured discount on the listed categories. Multiple
    overlapping windows take the max."""
    hour = arrival_minute / 60.0
    best = 0.0
    for hh in state.get("happy_hours", []):
        if not (hh["from_date"] <= date <= hh["until_date"]):
            continue
        if not (hh["start_hour"] <= hour < hh["end_hour"]):
            continue
        if hh["categories"] and category not in hh["categories"]:
            continue
        best = max(best, float(hh["discount_pct"]))
    return best
