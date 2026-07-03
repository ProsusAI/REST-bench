# Copyright © 2026 MIH AI B.V.
# Licensed under the Apache License, Version 2.0.
# See LICENSE file in the project root for full license information.

"""Customer cohort dynamics — Phase 2.1.

Replaces the bare reputation-multiplier with a slow-moving population
model. Three tiers (regulars, occasionals, prospects) plus a `lost`
sink. Promotions and demotions are driven by the *previous* day's
satisfaction and walkouts, so the impact of a bad night spreads over
weeks. The cohort sizes contribute an additive demand lift on top of
the existing multipliers.

This is intentionally aggregate (no per-visit cohort tagging) so the
day-sim's hot loop stays fast. The agent observes only banded counts
via `manager_view` (Phase 4.1) so it can't game the exact populations.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np

from .tuning import COHORTS


def initial_cohorts() -> dict:
    return dict(COHORTS.INITIAL)


def cohort_demand_lift(cohorts: dict | None) -> float:
    """Multiplicative lift contributed by current cohort populations.

    A healthy regulars base provides a steady ~5–10% tailwind; a bled-out
    funnel (lots of `lost`, few prospects) drags ~15–20%. Clamped to
    the configured floor/ceiling.
    """
    if not cohorts:
        return 1.0
    raw = 1.0 + (
        cohorts.get("regulars", 0) * COHORTS.REGULAR_DEMAND_LIFT
        + cohorts.get("occasionals", 0) * COHORTS.OCCASIONAL_DEMAND_LIFT
        + cohorts.get("prospects", 0) * COHORTS.PROSPECT_DEMAND_LIFT
    )
    # Subtract the baseline cohort lift so a healthy starting state ≈ 1.0.
    baseline = 1.0 + (
        COHORTS.INITIAL["regulars"] * COHORTS.REGULAR_DEMAND_LIFT
        + COHORTS.INITIAL["occasionals"] * COHORTS.OCCASIONAL_DEMAND_LIFT
        + COHORTS.INITIAL["prospects"] * COHORTS.PROSPECT_DEMAND_LIFT
    )
    raw = raw / baseline
    return float(np.clip(raw, COHORTS.LIFT_FLOOR, COHORTS.LIFT_CEIL))


def update_cohorts(state: dict, mean_satisfaction: float,
                   walkouts: int, marketing_spend_eur: float) -> dict:
    """Advance the cohort populations one day.

    Returns the change vector for logging/observability.
    """
    if "cohorts" not in state or not state["cohorts"]:
        state["cohorts"] = initial_cohorts()
    c = state["cohorts"]
    before = dict(c)

    # --- Promotion (good days) ---
    if mean_satisfaction >= COHORTS.SAT_PROMOTE_THRESHOLD:
        strength = min(1.0, (mean_satisfaction - COHORTS.SAT_PROMOTE_THRESHOLD) / 0.20)
        promo_rate = COHORTS.PROMOTION_RATE * strength
        # Prospects → Occasionals first.
        promoted_p = int(c["prospects"] * promo_rate)
        c["prospects"] -= promoted_p
        c["occasionals"] += promoted_p
        # Then a smaller fraction of occasionals become regulars.
        promoted_o = int(c["occasionals"] * promo_rate * 0.5)
        c["occasionals"] -= promoted_o
        c["regulars"] += promoted_o

    # --- Demotion (bad days) ---
    if mean_satisfaction <= COHORTS.SAT_DEMOTE_THRESHOLD:
        strength = min(1.0, (COHORTS.SAT_DEMOTE_THRESHOLD - mean_satisfaction) / 0.40)
        demo_rate = COHORTS.DEMOTION_RATE * strength
        demoted_r = int(c["regulars"] * demo_rate)
        c["regulars"] -= demoted_r
        c["occasionals"] += demoted_r
        demoted_o = int(c["occasionals"] * demo_rate)
        c["occasionals"] -= demoted_o
        c["prospects"] += demoted_o
        demoted_p = int(c["prospects"] * demo_rate * 0.5)
        c["prospects"] -= demoted_p
        c["lost"] += demoted_p

    # --- Walkouts always hurt: skeptics get pushed out of the funnel ---
    walkout_demote = int(walkouts * COHORTS.WALKOUT_TO_LOST_FRACTION)
    pull_o = min(walkout_demote, c["occasionals"])
    c["occasionals"] -= pull_o
    c["prospects"] += pull_o
    rest = walkout_demote - pull_o
    pull_p = min(rest, c["prospects"])
    c["prospects"] -= pull_p
    c["lost"] += pull_p

    # --- Replenishment: word-of-mouth + marketing recover prospects from `lost` ---
    new_prospects = COHORTS.DAILY_NEW_PROSPECTS + int(
        marketing_spend_eur * COHORTS.MARKETING_PROSPECTS_PER_EUR
    )
    pulled_back = min(new_prospects, c["lost"])
    c["lost"] -= pulled_back
    c["prospects"] += pulled_back
    # Any surplus is genuinely new awareness.
    surplus = new_prospects - pulled_back
    c["prospects"] += surplus

    return {k: c[k] - before[k] for k in c}


def cohort_band(value: int, *, low: int, high: int) -> str:
    """Bucket a population count into a low/normal/high band for the
    masked manager_view (Phase 4.1) — the agent sees the trend, not the
    exact number."""
    if value < low:
        return "low"
    if value > high:
        return "high"
    return "normal"


def cohort_bands(cohorts: dict) -> dict:
    """Compute a banded view of cohorts based on the initial population.

    Bands are within ±50% of the starting size — a meaningful drift
    requires the agent to actually move customers.
    """
    initial = COHORTS.INITIAL
    return {
        tier: cohort_band(
            cohorts.get(tier, 0),
            low=int(initial[tier] * 0.6) if initial[tier] > 0 else 0,
            high=int(initial[tier] * 1.4) if initial[tier] > 0 else 100,
        )
        for tier in ("regulars", "occasionals", "prospects", "lost")
    }
