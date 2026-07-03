# Copyright © 2026 MIH AI B.V.
# Licensed under the Apache License, Version 2.0.
# See LICENSE file in the project root for full license information.

"""Single-day simulation that consumes GameState and returns per-day KPIs.

Adapted from simulate.py, but:
  * driven by GameState (prices, caps, promos, staffing, inventory, reputation, …)
  * updates inventory, cash, reputation, incidents, weather per day
  * meant to be called from the CLI's `advance` command
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from . import distributions as dists
from . import cohorts as cohort_mod
from . import reviews as review_mod
from .config import RESTAURANT, TABLES
from .economy import (
    staffing_pressure, payroll_for_date, fixed_costs_for_date,
    apply_spoilage, apply_ingredient_shock, marketing_lift,
    reputation_multiplier, update_reputation, sample_weather, roll_incident,
    roll_supplier_news,
)
from .game_state import (
    current_date, date_for, active_promo_discount, active_happy_hour_discount,
    effective_menu, log_decision,
)
from .tuning import (
    COMPETITOR, MARKETING, REVIEW, SATISFACTION, STAFF,
    SUPPLIER_NEWS, SUBSTITUTION, WAIT,
)


@dataclass
class TableState:
    id: str
    seats: int
    occupied_until: float = 0.0

    def is_free(self, now: float) -> bool:
        return self.occupied_until <= now


def pregenerate_reservations(state: dict, rng: np.random.Generator) -> None:
    """Pre-generate reservation intents for all days. Stored on state."""
    start = datetime.fromisoformat(state["start_date"])
    all_res: list[dict] = []
    rid = 0
    for day_index in range(state["total_days"]):
        dow = (start + timedelta(days=day_index)).weekday()
        expected = sum(dists.arrival_rate(dow, t) for t in range(11 * 60, 23 * 60))
        res_share = 0.55 if dow in (4, 5) else 0.40
        n_res = int(rng.poisson(expected * res_share))
        for _ in range(n_res):
            candidates = dists.sample_arrivals_for_day(rng, dow)
            if not candidates:
                continue
            booked_for = float(rng.choice(candidates))
            party = dists.sample_party_size(rng, booked_for)
            lead = dists.sample_reservation_lead_minutes(rng)
            no_show = dists.sample_no_show(rng, dow)
            rid += 1
            all_res.append({
                "reservation_id": f"R{rid:05d}",
                "day_index": day_index,
                "date": (start + timedelta(days=day_index)).date().isoformat(),
                "booked_for_minute": round(booked_for, 2),
                "lead_minutes": round(lead, 1),
                "party_size": party,
                "no_show": no_show,
                "made_at_day_index": max(0, day_index - int(lead // (24*60))),
            })
    state["reservations_all"] = all_res


def _meal_period(mins: float) -> str:
    return "lunch" if mins < 16 * 60 else "dinner"


def run_one_day(state: dict, rng: np.random.Generator) -> dict:
    """Advance state by one simulated day. Mutates state in place and
    returns the day's result dict."""
    day_index = state["day_index"]
    start = datetime.fromisoformat(state["start_date"])
    day_start = start + timedelta(days=day_index)
    dow = day_start.weekday()
    date_iso = day_start.date().isoformat()

    # --- Pre-day environmental state ---
    weather = sample_weather(rng, dow)
    state["weather_log"][date_iso] = weather
    incident = roll_incident(rng, date_iso)
    if incident:
        state["incidents_log"].append(incident)

    ingredient_shock = apply_ingredient_shock(state, rng)
    new_news = roll_supplier_news(state, rng)
    spoiled = apply_spoilage(state)

    # --- Effective menu for the day (after price overrides + promos) ---
    menu = effective_menu(state)
    menu_by_cat: dict[str, list[dict]] = {}
    for m in menu:
        menu_by_cat.setdefault(m["category"], []).append(m)
    for cat in menu_by_cat:
        menu_by_cat[cat].sort(key=lambda x: -x["popularity_weight"])

    # --- Floor plan (apply any table layout overrides) ---
    tables = [TableState(t.id, state["table_layout"].get(t.id, t.seats))
              for t in TABLES]

    # --- Effective demand multiplier ---
    spend = float(state["marketing_spend_by_day"].get(date_iso, 0.0))
    m_mult = marketing_lift(spend)
    r_mult = reputation_multiplier(state["reputation"])
    w_mult = weather["mult"]
    c_mult = COMPETITOR.ARRIVAL_MULT_WHEN_ACTIVE if state["competitor_active"] else 1.0
    l_mult = 1.0 + MARKETING.LOYALTY_ARRIVAL_LIFT if state["loyalty_enabled"] else 1.0
    cohort_mult = cohort_mod.cohort_demand_lift(state.get("cohorts"))
    demand_multiplier = m_mult * r_mult * w_mult * c_mult * l_mult * cohort_mult

    # --- Walk-in arrival stream (thinned NHPP, scaled by demand multiplier) ---
    walk_ins = []
    for t in dists.sample_arrivals_for_day(rng, dow):
        if rng.random() < demand_multiplier:
            walk_ins.append((t, None))
    # Avoid double-counting reservation demand
    res_share = 0.55 if dow in (4, 5) else 0.40
    walk_ins = [(t, rid) for (t, rid) in walk_ins if rng.random() > res_share]

    # --- Reservations for today, respecting cap and dropping no-shows ---
    today_res = [r for r in state["reservations_all"] if r["day_index"] == day_index]
    cap = state["reservation_caps"].get(date_iso)
    if cap is not None and len(today_res) > cap:
        today_res = sorted(today_res, key=lambda r: r["made_at_day_index"])[:cap]

    res_arrivals = []
    for r in today_res:
        if r["no_show"]:
            continue
        actual = r["booked_for_minute"] + rng.normal(0, 4.0)
        res_arrivals.append((actual, r["reservation_id"]))

    all_arrivals = sorted(walk_ins + res_arrivals, key=lambda x: x[0])

    # --- Staffing / kitchen efficiency modifiers ---
    pressure = staffing_pressure(state, date_iso)        # ~1.0 baseline
    staff_eff_base = min(STAFF.EFFICIENCY_CEIL, pressure)
    kitchen_eff = 1.0
    satisfaction_floor_penalty = 0.0
    if pressure < SATISFACTION.UNDERSTAFF_PENALTY_THRESHOLD:
        satisfaction_floor_penalty += (
            (SATISFACTION.UNDERSTAFF_PENALTY_THRESHOLD - pressure)
            * SATISFACTION.UNDERSTAFF_PENALTY_SLOPE
        )
    if incident:
        kitchen_eff *= incident.get("kitchen_efficiency", 1.0)
        staff_eff_base *= incident.get("staff_efficiency", 1.0)

    # Inventory outage tracking
    outages = []

    # Accumulators
    visits, orders, order_items, events = [], [], [], []
    day_revenue = 0.0
    day_food_cost = 0.0
    day_covers = 0
    day_visits_n = 0
    day_walkouts = 0
    day_discounts = 0.0
    hour_heat: dict[int, int] = {}
    sat_values: list[float] = []

    visit_counter = len(state["daily_kpis_history"]) * 200  # loose numbering
    order_counter = visit_counter

    # Track inventory locally during the day so we can decrement as we go
    inv = state["inventory"]

    for arrival_min, reservation_id in all_arrivals:
        if arrival_min < 11 * 60 or arrival_min >= 23 * 60:
            continue

        party_size = None
        if reservation_id is not None:
            r = next((r for r in today_res if r["reservation_id"] == reservation_id), None)
            if r:
                party_size = r["party_size"]
        if party_size is None:
            party_size = dists.sample_party_size(rng, arrival_min)

        chosen_table = None
        seated_at = None
        wait_tolerance = dists.sample_wait_tolerance(rng, arrival_min)
        # Unhappy reputation shortens patience
        rep_scale = (
            WAIT.REPUTATION_TOLERANCE_INTERCEPT
            + WAIT.REPUTATION_TOLERANCE_SLOPE * (state["reputation"] - 3.0)
        )
        wait_tolerance *= max(
            WAIT.REPUTATION_TOLERANCE_FLOOR,
            min(WAIT.REPUTATION_TOLERANCE_CEIL, rep_scale),
        )
        seat_attempt = arrival_min
        while True:
            candidates = [t for t in tables
                          if t.is_free(seat_attempt) and t.seats >= party_size]
            if candidates:
                candidates.sort(key=lambda t: (t.seats, t.id))
                chosen_table = candidates[0]
                seated_at = seat_attempt
                break
            feasible = [t.occupied_until for t in tables if t.seats >= party_size]
            if not feasible:
                day_walkouts += 1
                events.append({"ts": f"{date_iso}T{int(arrival_min//60):02d}:{int(arrival_min%60):02d}:00",
                               "event": "walkout_no_fit", "party_size": party_size})
                break
            next_free = min(feasible)
            if next_free - arrival_min > wait_tolerance:
                day_walkouts += 1
                events.append({"ts": f"{date_iso}T{int(arrival_min//60):02d}:{int(arrival_min%60):02d}:00",
                               "event": "walkout_balked", "party_size": party_size,
                               "expected_wait_min": round(next_free - arrival_min, 1)})
                break
            seat_attempt = next_free

        if chosen_table is None:
            continue

        seating_wait = seated_at - arrival_min
        period = _meal_period(seated_at)

        # --- Order generation ---
        item_counts = dists.sample_item_counts(rng, party_size, period)
        staff_eff = dists.sample_staff_efficiency(rng) * staff_eff_base
        if staff_eff < STAFF.EFFICIENCY_FLOOR:
            staff_eff = STAFF.EFFICIENCY_FLOOR

        order_counter += 1
        order_id = f"O{day_index:02d}{order_counter:06d}"
        order_lines: list[dict] = []
        subtotal_food = 0.0
        subtotal_drink = 0.0
        food_cost_total = 0.0
        discount_total = 0.0
        kitchen_times = []
        outage_hit = False
        substituted = False  # Phase 2.3: separate flag for sat tax accounting

        # Phase 2.3 — substitution fallbacks: if a guest's chosen category
        # is fully out, try the next-best category with similar role.
        FALLBACK = {
            "main": ["main", "appetizer"],
            "appetizer": ["appetizer", "side"],
            "side": ["side", "appetizer"],
            "dessert": ["dessert", "drink"],
            "drink": ["drink"],
        }

        for cat, count in item_counts.items():
            if count == 0 or cat not in menu_by_cat:
                continue
            cat_items = []
            served_cat = cat
            for fallback_cat in FALLBACK.get(cat, [cat]):
                if fallback_cat not in menu_by_cat:
                    continue
                candidates = [it for it in menu_by_cat[fallback_cat]
                              if inv.get(it["id"], 0) > 0]
                if candidates:
                    cat_items = candidates
                    served_cat = fallback_cat
                    if served_cat != cat:
                        substituted = True
                        outages.append({"date": date_iso, "category": cat,
                                        "substituted_with": served_cat})
                    break
            if not cat_items:
                outage_hit = True
                outages.append({"date": date_iso, "category": cat})
                continue
            cat = served_cat  # use the served category from here on
            weights = np.array([m["popularity_weight"] for m in cat_items])
            weights /= weights.sum()
            draws = rng.choice(len(cat_items), size=count, p=weights)
            counts = Counter(int(x) for x in draws)
            for idx, qty in counts.items():
                item = cat_items[idx]
                # cap quantity to stock
                stock = inv.get(item["id"], 0)
                qty = min(int(qty), stock)
                if qty <= 0:
                    outage_hit = True
                    continue
                inv[item["id"]] = stock - qty
                unit_price = item["price_eur"]
                # Apply active promo + happy-hour (capped at 50%, additive)
                promo_disc = active_promo_discount(state, cat, date_iso)
                hh_disc = active_happy_hour_discount(state, cat, date_iso, arrival_min)
                disc = min(0.5, promo_disc + hh_disc)
                line_discount = unit_price * disc * qty
                line_subtotal = unit_price * qty - line_discount
                line_food_cost = item["food_cost_eur"] * qty
                food_cost_total += line_food_cost
                discount_total += line_discount
                if cat == "drink":
                    subtotal_drink += line_subtotal
                else:
                    subtotal_food += line_subtotal
                for _ in range(qty):
                    kitchen_times.append(
                        dists.sample_prep_time(rng, cat) / (staff_eff * kitchen_eff)
                    )
                order_lines.append({
                    "order_id": order_id, "menu_item_id": item["id"],
                    "menu_item_name": item["name"], "category": cat,
                    "quantity": int(qty), "unit_price_eur": unit_price,
                    "line_discount_eur": round(line_discount, 2),
                    "line_total_eur": round(line_subtotal, 2),
                    "line_food_cost_eur": round(line_food_cost, 2),
                })

        # If nothing could be ordered (full outage), treat as a walkout.
        if not order_lines:
            day_walkouts += 1
            events.append({"ts": f"{date_iso}T{int(seated_at//60):02d}:{int(seated_at%60):02d}:00",
                           "event": "walkout_no_stock", "party_size": party_size,
                           "table_id": chosen_table.id})
            # Occupy the table briefly so it shows the disruption
            chosen_table.occupied_until = seated_at + 10.0
            continue

        kitchen_wait = max(kitchen_times) if kitchen_times else 0.0
        dining_duration = dists.sample_dining_duration(rng, party_size, period)
        dining_duration = max(dining_duration, kitchen_wait + 15.0)
        left_at = seated_at + dining_duration
        chosen_table.occupied_until = left_at

        subtotal = subtotal_food + subtotal_drink
        food_tax = subtotal_food * RESTAURANT["tax_rate"]
        drink_tax = subtotal_drink * RESTAURANT["drink_tax_rate"]
        tax = food_tax + drink_tax
        pre_tip = subtotal

        sat = dists.sample_satisfaction(rng, seating_wait, kitchen_wait)
        if outage_hit:
            sat *= SATISFACTION.OUTAGE_HIT_MULTIPLIER
        if substituted:
            sat *= (1.0 - SUBSTITUTION.SAT_TAX)
        sat = max(0.0, sat - satisfaction_floor_penalty)
        sat_values.append(sat)
        review_mod.queue_review(state, day_index, sat, rng)

        if party_size >= RESTAURANT["auto_gratuity_party_threshold"]:
            tip_pct = RESTAURANT["auto_gratuity_rate"]
        else:
            service_quality = 0.85 + 0.20 * sat
            tip_pct = dists.sample_tip_pct(rng, service_quality)
        tip = pre_tip * tip_pct
        total_paid = pre_tip + tip

        day_revenue += pre_tip
        day_food_cost += food_cost_total
        day_covers += party_size
        day_visits_n += 1
        day_discounts += discount_total
        hour_heat[int(arrival_min // 60)] = hour_heat.get(int(arrival_min // 60), 0) + 1

        visit_counter += 1
        visit_id = f"V{day_index:02d}{visit_counter:06d}"
        visits.append({
            "visit_id": visit_id, "order_id": order_id,
            "reservation_id": reservation_id, "date": date_iso,
            "arrival_minute": round(arrival_min, 2),
            "seating_wait_min": round(seating_wait, 2),
            "dining_duration_min": round(dining_duration, 2),
            "kitchen_wait_min": round(kitchen_wait, 2),
            "party_size": party_size, "table_id": chosen_table.id,
            "meal_period": period, "is_reservation": reservation_id is not None,
            "satisfaction": round(sat, 3),
        })
        orders.append({
            "order_id": order_id, "visit_id": visit_id, "date": date_iso,
            "subtotal_food_eur": round(subtotal_food, 2),
            "subtotal_drink_eur": round(subtotal_drink, 2),
            "subtotal_eur": round(subtotal, 2),
            "discount_eur": round(discount_total, 2),
            "tax_eur": round(tax, 2), "tip_eur": round(tip, 2),
            "tip_pct": round(tip_pct, 4),
            "total_paid_eur": round(total_paid, 2),
            "food_cost_eur": round(food_cost_total, 2),
            "gross_margin_eur": round(subtotal - food_cost_total, 2),
        })
        order_items.extend(order_lines)

    # --- End-of-day economics ---
    mean_sat = float(np.mean(sat_values)) if sat_values else SATISFACTION.DEFAULT_DAY_VALUE_NO_VISITS

    # Phase 2.2: walkouts may post 0-star ghost reviews.
    for _ in range(day_walkouts):
        review_mod.queue_ghost_walkout_review(state, day_index, rng)
    # Phase 2.2: post all reviews due today; reputation EWMAs over them.
    posted_sats, posted_count = review_mod.post_due_reviews(state, day_index)
    posted_mean_sat = float(np.mean(posted_sats)) if posted_sats else None

    if incident and "reputation_delta" in incident:
        from .tuning import REPUTATION as _REP
        state["reputation"] = round(
            max(_REP.MIN, min(_REP.MAX, state["reputation"] + incident["reputation_delta"])),
            3,
        )

    payroll = payroll_for_date(state, date_iso)
    fixed = fixed_costs_for_date(state, date_iso)
    fixed_total = sum(fixed.values())
    incident_cost = incident["cost"] if incident else 0.0
    marketing_cost = spend
    cogs_cash = day_food_cost  # simplification: pay COGS as realized

    cash_delta = (day_revenue
                  - payroll - fixed_total - incident_cost
                  - marketing_cost - cogs_cash)
    state["cash_eur"] = round(state["cash_eur"] + cash_delta, 2)

    # --- Bankruptcy check (Phase 1.2) ---
    from .tuning import SCORE as _SCORE
    if (not state.get("bankrupt", False)
            and state["cash_eur"] < _SCORE.BANKRUPTCY_THRESHOLD_EUR):
        state["bankrupt"] = True
        state["bankrupt_on_date"] = date_iso

    # --- Cohort dynamics (Phase 2.1) ---
    cohort_delta = cohort_mod.update_cohorts(
        state,
        mean_satisfaction=mean_sat,
        walkouts=day_walkouts,
        marketing_spend_eur=marketing_cost,
    )

    pnl_row = {
        "date": date_iso, "day_index": day_index,
        "revenue_eur": round(day_revenue, 2),
        "cogs_eur": round(day_food_cost, 2),
        "gross_margin_eur": round(day_revenue - day_food_cost, 2),
        "payroll_eur": payroll,
        "rent_eur": fixed["rent"], "utilities_eur": fixed["utilities"],
        "insurance_eur": fixed["insurance"], "pos_eur": fixed["pos"],
        "loyalty_eur": fixed["loyalty_program"],
        "marketing_eur": marketing_cost,
        "incident_eur": incident_cost,
        "discounts_eur": round(day_discounts, 2),
        "net_profit_eur": round(cash_delta, 2),
        "cash_end_eur": state["cash_eur"],
    }
    state["pnl_history"].append(pnl_row)

    kpi = {
        "date": date_iso, "day_index": day_index, "day_of_week": dow,
        "day_name": day_start.strftime("%A"),
        "visits": day_visits_n, "covers": day_covers,
        "walkouts": day_walkouts, "avg_check_eur":
            round(day_revenue / day_visits_n, 2) if day_visits_n else 0,
        "revenue_eur": round(day_revenue, 2),
        "gross_margin_eur": round(day_revenue - day_food_cost, 2),
        "mean_satisfaction": round(mean_sat, 3),
        "reputation": state["reputation"],
        "weather": weather["condition"],
        "demand_multiplier": round(demand_multiplier, 3),
        "staffing_pressure": round(pressure, 3),
        "net_profit_eur": round(cash_delta, 2),
        "cash_end_eur": state["cash_eur"],
        "hour_heat": hour_heat,
        "reservations_today": len(today_res),
        "cap_applied": cap,
        "incident": incident["kind"] if incident else None,
        "spoilage_units": int(sum(spoiled.values())),
        "ingredient_shock": ingredient_shock,
        # Phase 2.x metrics
        "reviews_posted": posted_count,
        "reviews_posted_mean_sat": (
            round(posted_mean_sat, 3) if posted_mean_sat is not None else None
        ),
        "cohorts": dict(state.get("cohorts", {})),
        "cohort_delta": cohort_delta,
        "cohort_demand_lift": round(cohort_mult, 3),
    }
    state["daily_kpis_history"].append(kpi)
    state["day_index"] += 1

    return {
        "kpi": kpi, "pnl": pnl_row, "visits": visits, "orders": orders,
        "order_items": order_items, "events": events,
        "weather": weather, "incident": incident, "outages": outages,
    }
