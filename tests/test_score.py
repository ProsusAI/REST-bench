# Copyright © 2026 MIH AI B.V.
# Licensed under the Apache License, Version 2.0.
# See LICENSE file in the project root for full license information.

"""Tests for the Phase-1 walkout-aware, smooth-penalty score."""
from __future__ import annotations

import pytest

from rest_sim.observability import compute_score
from rest_sim.tuning import SCORE


def test_baseline_no_walkouts_no_gaps_equals_profit():
    s = compute_score(
        total_profit_eur=10_000,
        mean_satisfaction=SCORE.TARGET_SATISFACTION,
        mean_reputation=SCORE.TARGET_REPUTATION,
        total_walkouts=0, bankrupt=False,
    )
    assert s["score_eur"] == 10_000
    assert s["satisfaction_penalty_eur"] == 0
    assert s["reputation_penalty_eur"] == 0
    assert s["walkout_penalty_eur"] == 0


def test_walkouts_subtract_linearly():
    s = compute_score(
        total_profit_eur=10_000,
        mean_satisfaction=SCORE.TARGET_SATISFACTION,
        mean_reputation=SCORE.TARGET_REPUTATION,
        total_walkouts=1000, bankrupt=False,
    )
    expected = 10_000 - 1000 * SCORE.WALKOUT_PENALTY_PER_VISIT_EUR
    assert s["score_eur"] == expected


def test_satisfaction_penalty_smooth_quadratic():
    """Smooth penalty: a 0.10-gap should be 4× a 0.05-gap (squared)."""
    small = compute_score(
        total_profit_eur=0,
        mean_satisfaction=SCORE.TARGET_SATISFACTION - 0.05,
        mean_reputation=SCORE.TARGET_REPUTATION,
        total_walkouts=0, bankrupt=False,
    )["satisfaction_penalty_eur"]
    big = compute_score(
        total_profit_eur=0,
        mean_satisfaction=SCORE.TARGET_SATISFACTION - 0.10,
        mean_reputation=SCORE.TARGET_REPUTATION,
        total_walkouts=0, bankrupt=False,
    )["satisfaction_penalty_eur"]
    assert big == pytest.approx(4 * small, rel=1e-3)


def test_no_penalty_above_target():
    """Above-target satisfaction/reputation should not pay a bonus."""
    above = compute_score(
        total_profit_eur=0,
        mean_satisfaction=0.99,
        mean_reputation=4.99,
        total_walkouts=0, bankrupt=False,
    )
    assert above["satisfaction_penalty_eur"] == 0
    assert above["reputation_penalty_eur"] == 0
    assert above["score_eur"] == 0


def test_bankruptcy_floors_score():
    s = compute_score(
        total_profit_eur=999_999,
        mean_satisfaction=0.99, mean_reputation=4.99,
        total_walkouts=0, bankrupt=True,
    )
    assert s["bankrupt"] is True
    assert s["score_eur"] == SCORE.BANKRUPT_SCORE_EUR


def test_baseline_no_action_run_scores_negative(game_dir):
    """The 30-day no-decision run scores meaningfully negative under the
    new metric (walkouts dominate). Captured baseline: ~-€38k."""
    import numpy as np
    from rest_sim import game_state as gs
    from rest_sim.day_sim import pregenerate_reservations, run_one_day
    from rest_sim.observability import scorecard

    state = gs.default_state(seed=20260424, start_date="2026-04-24",
                             total_days=30, starting_cash=20000.0)
    pregenerate_reservations(state, np.random.default_rng(20260424))
    for _ in range(30):
        run_one_day(state,
                    np.random.default_rng(state["seed"] + state["day_index"] * 1_000_003))
    sc = scorecard(state)
    assert sc["score_eur"] < -20_000, sc
    assert sc["walkout_penalty_eur"] > 20_000, sc
    assert not sc["bankrupt"]


def test_bankruptcy_triggers_when_cash_low(game_dir):
    """A simulated catastrophe drives cash below threshold and halts the sim."""
    import numpy as np
    from rest_sim import game_state as gs
    from rest_sim.day_sim import pregenerate_reservations, run_one_day

    state = gs.default_state(seed=1, start_date="2026-04-24",
                             total_days=10, starting_cash=100.0)
    pregenerate_reservations(state, np.random.default_rng(1))
    # Disable inventory so revenue collapses; massive marketing spend bleeds cash.
    state["inventory"] = {k: 0 for k in state["inventory"]}
    state["marketing_spend_by_day"] = {
        gs.date_for(state, i): 5_000.0 for i in range(10)
    }
    for _ in range(10):
        run_one_day(state,
                    np.random.default_rng(state["seed"] + state["day_index"] * 1_000_003))
        if state.get("bankrupt"):
            break
    assert state["bankrupt"], (
        f"expected bankruptcy under marketing-bleed scenario; cash={state['cash_eur']}"
    )
    assert state["bankrupt_on_date"] is not None
