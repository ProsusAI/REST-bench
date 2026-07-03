# Copyright © 2026 MIH AI B.V.
# Licensed under the Apache License, Version 2.0.
# See LICENSE file in the project root for full license information.

"""Golden-run regression test.

A 30-day no-decision simulation with a fixed seed must produce a known
scorecard. This is the safety net for every refactor in the codebase —
if a change moves the numbers below, the change has altered mechanics
(intentionally or not) and the golden values must be re-baselined.

Re-baseline procedure when a phase intentionally changes mechanics:
    1. Run `pytest tests/test_golden_run.py -v` and copy the printed actuals.
    2. Replace the GOLDEN_* constants below.
    3. Bump GOLDEN_PHASE_TAG so the change is visible in git history.

Tolerance is tight (single-digit euros) because the simulation is fully
seeded — drift indicates a bug, not RNG noise.
"""
from __future__ import annotations

import numpy as np
import pytest

from rest_sim import game_state as gs
from rest_sim.day_sim import pregenerate_reservations, run_one_day
from rest_sim.observability import scorecard


GOLDEN_PHASE_TAG = "phase-2"
GOLDEN_SEED = 20260424
GOLDEN_START_DATE = "2026-04-24"
GOLDEN_DAYS = 30
GOLDEN_STARTING_CASH = 20000.0

# Captured from a fresh `init` + 30× `advance` with the seed above
# AFTER Phase 2 (cohorts + delayed reviews + scheduled supplier news).
# Reputation now drifts down under ghost walkout reviews — the no-action
# baseline genuinely fails on reputation, walkouts, *and* profit.
GOLDEN_SCORECARD = {
    "days_simulated": 30,
    "starting_cash_eur": 20000.0,
    "ending_cash_eur": 16027.95,
    "cash_delta_eur": -3972.05,
    "total_revenue_eur": 101597.5,
    "total_cogs_eur": 27614.57,
    "total_net_profit_eur": -3972.06,
    "gross_margin_pct": 0.728,
    "mean_satisfaction": 0.749,
    "mean_reputation": 3.543,
    "total_walkouts": 3636,
    "incidents": 4,
    "decisions": 0,
}


def _run_no_decision_month():
    state = gs.default_state(
        seed=GOLDEN_SEED, start_date=GOLDEN_START_DATE,
        total_days=GOLDEN_DAYS, starting_cash=GOLDEN_STARTING_CASH,
    )
    pregenerate_reservations(state, np.random.default_rng(GOLDEN_SEED))
    for _ in range(GOLDEN_DAYS):
        day_seed = state["seed"] + state["day_index"] * 1_000_003
        run_one_day(state, np.random.default_rng(day_seed))
    return state


def test_golden_run_scorecard_matches(game_dir):
    """30-day no-decision run must reproduce the captured scorecard exactly."""
    state = _run_no_decision_month()
    actual = scorecard(state)
    # Strict eur-cent equality on monetary fields, tight tolerance on rates.
    for k, expected in GOLDEN_SCORECARD.items():
        got = actual[k]
        if isinstance(expected, float):
            assert got == pytest.approx(expected, abs=0.01), (
                f"{k}: expected {expected}, got {got} "
                f"(actual scorecard: {actual})"
            )
        else:
            assert got == expected, (
                f"{k}: expected {expected}, got {got} "
                f"(actual scorecard: {actual})"
            )


def test_golden_run_is_deterministic(game_dir):
    """Two independent runs with the same seed must be byte-identical."""
    s1 = scorecard(_run_no_decision_month())
    s2 = scorecard(_run_no_decision_month())
    assert s1 == s2


def test_state_save_load_strips_reservations(game_dir, tmp_path):
    """Phase 0.4 invariant: state.json never contains reservations_all."""
    import json
    import os

    state = gs.default_state(
        seed=GOLDEN_SEED, start_date=GOLDEN_START_DATE,
        total_days=5, starting_cash=10000.0,
    )
    pregenerate_reservations(state, np.random.default_rng(GOLDEN_SEED))
    assert state["reservations_all"], "fixture sanity: reservations should exist in memory"
    gs.save(state)
    with open(os.path.join(game_dir, "state.json")) as f:
        on_disk = json.load(f)
    assert "reservations_all" not in on_disk, (
        "reservations_all leaked into state.json — agent could read it directly"
    )
    # Sidecar must exist and be reloadable transparently.
    assert os.path.exists(os.path.join(game_dir, "reservations.json"))
    reloaded = gs.load()
    assert reloaded["reservations_all"] == state["reservations_all"]
