"""Tests for Phase-2 simulation mechanics: cohorts, reviews, supplier news."""
from __future__ import annotations

import numpy as np
import pytest

from rest_sim import cohorts as cohort_mod
from rest_sim import reviews as review_mod
from rest_sim import game_state as gs
from rest_sim.economy import roll_supplier_news, apply_ingredient_shock
from rest_sim.tuning import COHORTS, REVIEW, SUPPLIER_NEWS


def _state():
    return gs.default_state(seed=1, start_date="2026-04-24",
                            total_days=30, starting_cash=20000.0)


# --- Cohorts ---------------------------------------------------------------

def test_cohorts_initial_lift_is_neutral():
    s = _state()
    assert cohort_mod.cohort_demand_lift(s["cohorts"]) == pytest.approx(1.0, abs=0.01)


def test_cohorts_promote_on_great_days():
    s = _state()
    initial_regulars = s["cohorts"]["regulars"]
    for _ in range(20):
        cohort_mod.update_cohorts(s, mean_satisfaction=0.95,
                                  walkouts=0, marketing_spend_eur=200)
    assert s["cohorts"]["regulars"] > initial_regulars
    # Demand lift visibly improves with a stronger regulars base.
    assert cohort_mod.cohort_demand_lift(s["cohorts"]) > 1.0


def test_cohorts_demote_on_bad_days():
    s = _state()
    initial_lift = cohort_mod.cohort_demand_lift(s["cohorts"])
    for _ in range(20):
        cohort_mod.update_cohorts(s, mean_satisfaction=0.30,
                                  walkouts=200, marketing_spend_eur=0)
    assert s["cohorts"]["lost"] > 0
    # Population erosion drags arrivals.
    assert cohort_mod.cohort_demand_lift(s["cohorts"]) < initial_lift


def test_cohort_lift_clamped():
    extreme_high = {"regulars": 99999, "occasionals": 99999, "prospects": 99999, "lost": 0}
    extreme_low = {"regulars": 0, "occasionals": 0, "prospects": 0, "lost": 99999}
    assert cohort_mod.cohort_demand_lift(extreme_high) == COHORTS.LIFT_CEIL
    # Floor is a defensive bound; an empty funnel still sits above the floor.
    assert COHORTS.LIFT_FLOOR <= cohort_mod.cohort_demand_lift(extreme_low) < 1.0


def test_cohort_bands_classify():
    s = _state()
    bands = cohort_mod.cohort_bands(s["cohorts"])
    assert bands["regulars"] == "normal"
    bands_high = cohort_mod.cohort_bands(
        {"regulars": 200, "occasionals": 320, "prospects": 1500, "lost": 0}
    )
    assert bands_high["regulars"] == "high"


# --- Reviews ---------------------------------------------------------------

def test_review_queue_lag_within_bounds():
    s = _state()
    rng = np.random.default_rng(0)
    for _ in range(2000):
        review_mod.queue_review(s, day_index=0, satisfaction=0.85, rng=rng)
    if s["review_queue"]:
        lags = [r["post_on_day_index"] for r in s["review_queue"]]
        assert min(lags) >= 0
        assert max(lags) <= REVIEW.LAG_MAX_DAYS


def test_post_due_reviews_drives_reputation():
    s = _state()
    s["reputation"] = 4.2
    # Queue 100 perfect reviews due today.
    for _ in range(100):
        s["review_queue"].append({"post_on_day_index": 5, "satisfaction": 0.99})
    rep_before = s["reputation"]
    sats, n = review_mod.post_due_reviews(s, day_index=5)
    assert n == 100
    assert s["reputation"] >= rep_before  # perfect reviews push up


def test_ghost_walkout_reviews_drag_reputation():
    s = _state()
    rng = np.random.default_rng(0)
    # 1000 walkouts → some fraction post 0-star ghost reviews.
    for _ in range(1000):
        review_mod.queue_ghost_walkout_review(s, day_index=0, rng=rng)
    n_ghosts = len(s["review_queue"])
    assert 0 < n_ghosts < 1000  # probabilistic; should be ~5%


# --- Supplier news ---------------------------------------------------------

def test_roll_supplier_news_emits_within_window():
    s = _state()
    rng = np.random.default_rng(0)
    posted = 0
    for _ in range(100):
        item = roll_supplier_news(s, rng)
        if item is not None:
            posted += 1
            assert item["lead_days"] in SUPPLIER_NEWS.LEAD_TIMES_DAYS
            assert item["fire_on_date"] > item["posted_on"]
    assert posted > 0


def test_scheduled_shock_fires_on_due_date():
    s = _state()
    # Manually queue a shock for today.
    today = gs.current_date(s)
    s["supplier_news"] = [{
        "posted_on": today, "fire_on_date": today, "lead_days": 0,
        "predicted_direction": +1,
        "predicted_magnitude_center": 0.20,
        "predicted_magnitude_sigma": 0.01,
        "predicted_magnitude_range": [0.18, 0.22],
        "confidence": 0.9, "headline": "test",
    }]
    pre = s["ingredient_price_multiplier"]
    apply_ingredient_shock(s, np.random.default_rng(0))
    assert s["ingredient_price_multiplier"] > pre  # +20% direction
    assert s["supplier_news"] == []  # consumed
    assert s["supplier_news_history"][0]["realized_shock"] is not None
