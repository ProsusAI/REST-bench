"""Report generators for the management game."""
from __future__ import annotations

from typing import Any

import numpy as np

from .tuning import SCORE


def pnl_summary(state: dict, last_n_days: int | None = None) -> dict:
    rows = state["pnl_history"]
    if last_n_days is not None:
        rows = rows[-last_n_days:]
    if not rows:
        return {"empty": True}
    agg = {k: 0.0 for k in [
        "revenue_eur", "cogs_eur", "gross_margin_eur", "payroll_eur",
        "rent_eur", "utilities_eur", "insurance_eur", "pos_eur",
        "loyalty_eur", "marketing_eur", "incident_eur", "discounts_eur",
        "net_profit_eur",
    ]}
    for r in rows:
        for k in agg:
            agg[k] += r[k]
    agg = {k: round(v, 2) for k, v in agg.items()}
    agg["days"] = len(rows)
    agg["cash_start"] = rows[0]["cash_end_eur"] - rows[0]["net_profit_eur"]
    agg["cash_end"] = rows[-1]["cash_end_eur"]
    return agg


def attribution(state: dict) -> dict:
    """Compare the last 7 days vs the prior 7 days: what drove the delta?"""
    h = state["daily_kpis_history"]
    if len(h) < 8:
        return {"note": f"need at least 8 days of history (have {len(h)})"}
    last = h[-7:]
    prior = h[-14:-7] if len(h) >= 14 else h[:-7]

    def mean(rows, key):
        return float(np.mean([r[key] for r in rows])) if rows else 0.0

    def tot(rows, key):
        return float(np.sum([r[key] for r in rows])) if rows else 0.0

    delta_rev = tot(last, "revenue_eur") - tot(prior, "revenue_eur")
    delta_visits = tot(last, "visits") - tot(prior, "visits")
    delta_check = mean(last, "avg_check_eur") - mean(prior, "avg_check_eur")
    delta_walkouts = tot(last, "walkouts") - tot(prior, "walkouts")
    delta_sat = mean(last, "mean_satisfaction") - mean(prior, "mean_satisfaction")
    delta_rep = mean(last, "reputation") - mean(prior, "reputation")
    delta_demand = mean(last, "demand_multiplier") - mean(prior, "demand_multiplier")

    return {
        "window_last": f"{last[0]['date']} \u2192 {last[-1]['date']}",
        "window_prior": f"{prior[0]['date']} \u2192 {prior[-1]['date']}",
        "revenue_delta_eur": round(delta_rev, 2),
        "visits_delta": int(delta_visits),
        "avg_check_delta_eur": round(delta_check, 2),
        "walkouts_delta": int(delta_walkouts),
        "satisfaction_delta": round(delta_sat, 3),
        "reputation_delta": round(delta_rep, 3),
        "demand_multiplier_delta": round(delta_demand, 3),
    }


def heatmap(state: dict, last_n_days: int | None = None) -> dict:
    """Arrivals heatmap by day_of_week × hour from daily KPI hour_heat."""
    rows = state["daily_kpis_history"]
    if last_n_days:
        rows = rows[-last_n_days:]
    # dow (0-6) × hour (11-22) → count
    grid = {dow: {h: 0 for h in range(11, 23)} for dow in range(7)}
    for r in rows:
        dow = r["day_of_week"]
        for h_str, c in r["hour_heat"].items():
            h = int(h_str)
            if h in grid[dow]:
                grid[dow][h] += c
    # Render a small ASCII table
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    lines = ["hour " + " ".join(f"{d:>4}" for d in day_names)]
    for h in range(11, 23):
        row = [f"{h:>2}  "]
        for dow in range(7):
            row.append(f"{grid[dow][h]:>4}")
        lines.append(" ".join(row))
    return {"grid": grid, "ascii": "\n".join(lines)}


def cohort_breakdown(state: dict, last_n_days: int = 7) -> dict:
    """Reservation vs walk-in margin & volume from per-day KPIs.
    (Simple approximation — computed from visit counts; finer attribution
    would need to persist order-level cohorts.)"""
    # We store only aggregates by day in state, so return a stylized breakdown
    # using the reservations_all table.
    h = state["daily_kpis_history"][-last_n_days:]
    if not h:
        return {"empty": True}
    total_visits = sum(r["visits"] for r in h)
    total_res = sum(r["reservations_today"] for r in h)
    walk = total_visits - total_res
    return {
        "window_days": len(h),
        "reservation_visits_est": total_res,
        "walk_in_visits_est": max(0, walk),
        "reservation_share": round(total_res / total_visits, 3) if total_visits else 0.0,
    }


def _alerts(state: dict, next_date: str | None) -> list[str]:
    out = []
    if state.get("bankrupt", False):
        out.append(
            f"BANKRUPT on {state.get('bankrupt_on_date')} — simulation halted"
        )
    low = sorted(
        [(k, v) for k, v in state["inventory"].items() if v < 10],
        key=lambda x: x[1]
    )[:10]
    if low:
        out.append("low stock: " + ", ".join(f"{k}={v}" for k, v in low))
    if state["cash_eur"] < 2000:
        out.append(f"CASH LOW: €{state['cash_eur']:.0f}")
    if state["reputation"] < 3.8:
        out.append(f"reputation declining: {state['reputation']:.2f}")
    if state["ingredient_price_multiplier"] > 1.2:
        out.append(
            f"ingredient prices up {(state['ingredient_price_multiplier']-1)*100:.0f}%"
        )
    return out


def manager_view(state: dict) -> dict:
    """Phase 4.1 — masked status the agent sees each turn.

    A real restaurant manager doesn't see exact cohort counts, raw
    same-day diner satisfaction values, or future state. They see:
    - the booking system's near-term reservations (counts only)
    - reviews posted on review sites (lagging)
    - a vague sense of "we have lots of regulars" (banded)
    - supplier alerts (probabilistic, lead time)
    - last week's KPIs (not full history)
    """
    from . import game_state
    from . import cohorts as cohort_mod

    h = state["daily_kpis_history"]
    last = h[-1] if h else None
    last_7 = h[-7:] if h else []
    next_date = None
    if not game_state.is_done(state):
        next_date = game_state.current_date(state)

    # Strip the per-day cohort dump and raw same-day satisfaction from KPIs.
    masked_last_7 = []
    for r in last_7:
        masked_last_7.append({
            k: v for k, v in r.items()
            if k not in ("hour_heat", "cohorts", "cohort_delta")
        })

    cohort_sizes_band = (
        cohort_mod.cohort_bands(state["cohorts"]) if state.get("cohorts") else {}
    )

    # Supplier news: only show what an alert system would actually show.
    upcoming_news = [
        {
            "fire_on_date": n["fire_on_date"],
            "lead_days": n["lead_days"],
            "headline": n["headline"],
            "predicted_magnitude_range": n["predicted_magnitude_range"],
            "confidence": n["confidence"],
        }
        for n in state.get("supplier_news", [])
    ]

    return {
        "day_index": state["day_index"],
        "total_days": state["total_days"],
        "next_date": next_date,
        "cash_eur": state["cash_eur"],
        "reputation": state["reputation"],
        # The manager sees a public review-summary score, not the inner EWMA on
        # raw diner satisfaction — that's an internal engine signal.
        "satisfaction_ewma": round(state["satisfaction_ewma"], 3),
        "ingredient_price_multiplier": round(state["ingredient_price_multiplier"], 3),
        "loyalty_enabled": state["loyalty_enabled"],
        "competitor_active": state["competitor_active"],
        "active_promos": state["active_promos"],
        "active_happy_hours": state.get("happy_hours", []),
        "upcoming_staffing_overrides": {
            d: v for d, v in state["staffing_override"].items()
            if d >= (next_date or "")
        },
        "upcoming_reservation_caps": {
            d: v for d, v in state["reservation_caps"].items()
            if d >= (next_date or "")
        },
        "cohort_sizes_band": cohort_sizes_band,
        "supplier_news": upcoming_news,
        "reviews_posted_today": last["reviews_posted"] if last else 0,
        "last_7_days_kpis": masked_last_7,
        "alerts": _alerts(state, next_date),
    }


def status_snapshot(state: dict, *, full: bool = False) -> dict:
    """Status for the agent's turn.

    By default returns the masked `manager_view`. Pass `full=True` to
    get the legacy unfiltered snapshot (used by tests and the dashboard
    backend — agents must NOT use this).
    """
    if not full:
        return manager_view(state)

    from . import game_state
    h = state["daily_kpis_history"]
    last = h[-1] if h else None
    next_date = None
    if not game_state.is_done(state):
        next_date = game_state.current_date(state)
    return {
        "day_index": state["day_index"],
        "total_days": state["total_days"],
        "next_date": next_date,
        "cash_eur": state["cash_eur"],
        "reputation": state["reputation"],
        "satisfaction_ewma": round(state["satisfaction_ewma"], 3),
        "ingredient_price_multiplier": round(state["ingredient_price_multiplier"], 3),
        "loyalty_enabled": state["loyalty_enabled"],
        "competitor_active": state["competitor_active"],
        "active_promos": state["active_promos"],
        "active_happy_hours": state.get("happy_hours", []),
        "cohorts": state.get("cohorts", {}),
        "supplier_news": state.get("supplier_news", []),
        "review_queue_length": len(state.get("review_queue", [])),
        "upcoming_staffing_overrides": {
            d: v for d, v in state["staffing_override"].items()
            if d >= (next_date or "")
        },
        "upcoming_reservation_caps": {
            d: v for d, v in state["reservation_caps"].items()
            if d >= (next_date or "")
        },
        "last_day_kpi": last,
        "alerts": _alerts(state, next_date),
    }


def compute_score(
    *, total_profit_eur: float, mean_satisfaction: float,
    mean_reputation: float, total_walkouts: int, bankrupt: bool,
) -> dict:
    """Smooth, walkout-aware composite score.

    Replaces the old cliff penalties (`max(0, 0.75 − sat) × 100k` etc.)
    with quadratic penalties that share the same gradient direction but
    avoid catastrophic-cliff incentives. Walkouts get an explicit linear
    cost (they were ignored before), and bankruptcy floors the score so
    the agent can't hail-mary into negative cash.
    """
    if bankrupt:
        return {
            "score_eur": float(SCORE.BANKRUPT_SCORE_EUR),
            "profit_term_eur": round(total_profit_eur, 2),
            "satisfaction_penalty_eur": 0.0,
            "reputation_penalty_eur": 0.0,
            "walkout_penalty_eur": 0.0,
            "bankrupt": True,
        }
    sat_gap = max(0.0, SCORE.TARGET_SATISFACTION - mean_satisfaction)
    rep_gap = max(0.0, SCORE.TARGET_REPUTATION - mean_reputation)
    sat_pen = (sat_gap ** 2) * SCORE.SATISFACTION_PENALTY_SCALE_EUR
    rep_pen = (rep_gap ** 2) * SCORE.REPUTATION_PENALTY_SCALE_EUR
    walk_pen = total_walkouts * SCORE.WALKOUT_PENALTY_PER_VISIT_EUR
    score = total_profit_eur - sat_pen - rep_pen - walk_pen
    return {
        "score_eur": round(score, 2),
        "profit_term_eur": round(total_profit_eur, 2),
        "satisfaction_penalty_eur": round(sat_pen, 2),
        "reputation_penalty_eur": round(rep_pen, 2),
        "walkout_penalty_eur": round(walk_pen, 2),
        "bankrupt": False,
    }


def scorecard(state: dict) -> dict:
    """Final outcome summary for the end of the month."""
    total_rev = sum(r["revenue_eur"] for r in state["pnl_history"])
    total_profit = sum(r["net_profit_eur"] for r in state["pnl_history"])
    total_cogs = sum(r["cogs_eur"] for r in state["pnl_history"])
    days = len(state["daily_kpis_history"])
    mean_sat = float(np.mean([r["mean_satisfaction"]
                              for r in state["daily_kpis_history"]])) if days else 0
    mean_rep = float(np.mean([r["reputation"]
                              for r in state["daily_kpis_history"]])) if days else 0
    total_walkouts = int(sum(r["walkouts"] for r in state["daily_kpis_history"]))
    score = compute_score(
        total_profit_eur=total_profit,
        mean_satisfaction=mean_sat,
        mean_reputation=mean_rep,
        total_walkouts=total_walkouts,
        bankrupt=bool(state.get("bankrupt", False)),
    )
    return {
        "days_simulated": days,
        "starting_cash_eur": state["starting_cash_eur"],
        "ending_cash_eur": state["cash_eur"],
        "cash_delta_eur": round(state["cash_eur"] - state["starting_cash_eur"], 2),
        "total_revenue_eur": round(total_rev, 2),
        "total_cogs_eur": round(total_cogs, 2),
        "total_net_profit_eur": round(total_profit, 2),
        "gross_margin_pct": round(1 - total_cogs / total_rev, 3) if total_rev else 0,
        "mean_satisfaction": round(mean_sat, 3),
        "mean_reputation": round(mean_rep, 3),
        "total_walkouts": total_walkouts,
        "incidents": len(state["incidents_log"]),
        "decisions": len(state["decisions_log"]),
        "bankrupt": bool(state.get("bankrupt", False)),
        **score,
    }
