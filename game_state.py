"""Persistent game state for the restaurant management simulation.

Single JSON file on disk. All mutations go through load/save.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any

from config import MENU_ITEMS, STAFF, TABLES


GAME_DIR = os.path.join(os.path.dirname(__file__), "game")
STATE_PATH = os.path.join(GAME_DIR, "state.json")


# Fixed costs (EUR)
RENT_PER_MONTH = 8000.0
UTILITIES_PER_DAY = 60.0
INSURANCE_PER_DAY = 10.0
POS_SUBSCRIPTION_PER_DAY = 5.0
LOYALTY_COST_PER_DAY = 50.0

# Default per-item starting inventory (units sellable before restock)
DEFAULT_STOCK = 250
PERISHABLE_CATEGORIES = {"appetizer", "main", "side", "dessert"}
PERISHABLE_SPOILAGE_RATE = 0.04  # 4% per day of stock spoils


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
        "reputation": 4.2,              # 1.0–5.0
        "satisfaction_ewma": 0.80,
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
    }


def exists() -> bool:
    return os.path.exists(STATE_PATH)


def load() -> dict:
    if not exists():
        raise FileNotFoundError(
            f"No game state at {STATE_PATH}. Run `python rest_cli.py init` first."
        )
    with open(STATE_PATH) as f:
        return json.load(f)


def save(state: dict) -> None:
    os.makedirs(GAME_DIR, exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, default=str)


def date_for(state: dict, day_index: int) -> str:
    d = datetime.fromisoformat(state["start_date"]).date() + timedelta(days=day_index)
    return d.isoformat()


def current_date(state: dict) -> str:
    return date_for(state, state["day_index"])


def is_done(state: dict) -> bool:
    return state["day_index"] >= state["total_days"]


def log_decision(state: dict, action: str, detail: dict) -> None:
    state["decisions_log"].append({
        "day_index": state["day_index"],
        "date": current_date(state) if not is_done(state) else None,
        "action": action,
        "detail": detail,
    })


def effective_menu(state: dict) -> list[dict]:
    """Menu dicts after applying price overrides, removals, additions, and
    the current ingredient price multiplier on food cost."""
    from config import build_menu
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
    from distributions import zipf_weights
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
