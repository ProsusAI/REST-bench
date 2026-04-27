"""Tests for Phase 4 partial-observability layer."""
from __future__ import annotations

import numpy as np

from rest_sim import game_state as gs
from rest_sim.day_sim import pregenerate_reservations, run_one_day
from rest_sim.observability import manager_view, status_snapshot


def _played_state(days: int = 5) -> dict:
    state = gs.default_state(seed=20260424, start_date="2026-04-24",
                             total_days=days, starting_cash=20000.0)
    pregenerate_reservations(state, np.random.default_rng(20260424))
    for _ in range(days):
        run_one_day(state, np.random.default_rng(
            state["seed"] + state["day_index"] * 1_000_003))
    return state


def test_manager_view_hides_full_history(game_dir):
    state = _played_state(days=10)
    mv = manager_view(state)
    assert "last_7_days_kpis" in mv
    assert len(mv["last_7_days_kpis"]) <= 7
    # Full state has all 10 — manager view should not.
    full = status_snapshot(state, full=True)
    assert "last_7_days_kpis" not in full or "last_day_kpi" in full


def test_manager_view_replaces_cohort_counts_with_bands(game_dir):
    state = _played_state(days=5)
    mv = manager_view(state)
    assert "cohort_sizes_band" in mv
    # No raw cohort counts leaked into the agent's view.
    for v in mv["cohort_sizes_band"].values():
        assert v in {"low", "normal", "high"}
    assert "cohorts" not in mv


def test_manager_view_omits_review_queue_internals(game_dir):
    state = _played_state(days=5)
    mv = manager_view(state)
    # The agent shouldn't see the pending reviews queue (future state).
    assert "review_queue" not in mv
    assert "reviews_posted_today" in mv


def test_full_status_includes_engine_internals(game_dir):
    state = _played_state(days=3)
    full = status_snapshot(state, full=True)
    assert "cohorts" in full
    assert "review_queue_length" in full
    assert "supplier_news" in full


def test_default_status_is_masked(game_dir):
    """The default `status_snapshot()` (no `full` flag) returns the masked view."""
    state = _played_state(days=3)
    default = status_snapshot(state)
    assert "cohort_sizes_band" in default
    assert "cohorts" not in default  # raw counts hidden
