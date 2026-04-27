"""Unit tests for pure functions in rest_sim.economy."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pytest

from rest_sim import game_state as gs
from rest_sim.economy import (
    apply_ingredient_shock,
    apply_spoilage,
    fixed_costs_for_date,
    marketing_lift,
    payroll_for_date,
    reputation_multiplier,
    restock_cost,
    roll_incident,
    sample_weather,
    scheduled_staff_for_date,
    staffing_pressure,
    update_reputation,
)


def _state(**overrides):
    s = gs.default_state(seed=1, start_date="2026-04-24",
                         total_days=30, starting_cash=20000.0)
    s.update(overrides)
    return s


def test_marketing_lift_zero_is_neutral():
    assert marketing_lift(0.0) == 1.0
    assert marketing_lift(-50.0) == 1.0


def test_marketing_lift_monotone_and_capped():
    assert marketing_lift(100) > marketing_lift(0)
    assert marketing_lift(1000) > marketing_lift(100)
    # Asymptote is 1 + 0.5 = 1.5
    assert marketing_lift(1e9) <= 1.5
    assert marketing_lift(1e9) > 1.49


def test_reputation_multiplier_anchored_at_4():
    assert reputation_multiplier(4.0) == pytest.approx(1.0)
    assert reputation_multiplier(5.0) > 1.0
    assert reputation_multiplier(3.0) < 1.0
    # Clamped to [0.6, 1.4]
    assert reputation_multiplier(0.0) == pytest.approx(0.6)
    assert reputation_multiplier(10.0) == pytest.approx(1.4)


def test_update_reputation_moves_toward_implied_score():
    s = _state(reputation=4.0, satisfaction_ewma=0.80)
    # High satisfaction should pull reputation up over many days.
    for _ in range(50):
        update_reputation(s, mean_satisfaction=0.95)
    assert s["reputation"] > 4.5
    # Low satisfaction should pull it down.
    s2 = _state(reputation=4.0, satisfaction_ewma=0.80)
    for _ in range(50):
        update_reputation(s2, mean_satisfaction=0.30)
    assert s2["reputation"] < 3.5


def test_payroll_includes_overrides():
    s = _state()
    base = payroll_for_date(s, "2026-04-24")  # Friday
    s["staffing_override"]["2026-04-24"] = {"line_cook": 99}
    boosted = payroll_for_date(s, "2026-04-24")
    assert boosted > base


def test_staffing_pressure_default_is_unity():
    s = _state()
    p = staffing_pressure(s, "2026-04-24")
    assert p == pytest.approx(1.0, abs=0.01)


def test_staffing_pressure_understaff_below_one():
    s = _state()
    s["staffing_override"]["2026-04-24"] = {"line_cook": 0, "server": 0}
    p = staffing_pressure(s, "2026-04-24")
    assert p < 1.0


def test_fixed_costs_rent_charged_only_on_first():
    s = _state()
    assert fixed_costs_for_date(s, "2026-04-01")["rent"] > 0
    assert fixed_costs_for_date(s, "2026-04-15")["rent"] == 0


def test_fixed_costs_loyalty_only_when_enabled():
    s = _state()
    assert fixed_costs_for_date(s, "2026-04-15")["loyalty_program"] == 0
    s["loyalty_enabled"] = True
    assert fixed_costs_for_date(s, "2026-04-15")["loyalty_program"] > 0


def test_apply_spoilage_reduces_perishables_only():
    s = _state()
    initial_main = s["inventory"]["M01"]   # main, perishable
    initial_drink = s["inventory"]["B01"]  # drink, non-perishable
    apply_spoilage(s)
    assert s["inventory"]["M01"] < initial_main
    assert s["inventory"]["B01"] == initial_drink


def test_apply_ingredient_shock_clamps_in_range():
    s = _state(ingredient_price_multiplier=1.0)
    rng = np.random.default_rng(0)
    for _ in range(2000):
        apply_ingredient_shock(s, rng)
        assert 0.6 <= s["ingredient_price_multiplier"] <= 2.0


def test_restock_cost_scales_with_qty_and_multiplier():
    s = _state(ingredient_price_multiplier=1.0)
    c1 = restock_cost(s, "M01", 10)
    c2 = restock_cost(s, "M01", 20)
    assert c2 == pytest.approx(c1 * 2)
    s["ingredient_price_multiplier"] = 1.5
    c3 = restock_cost(s, "M01", 10)
    # Each call rounds to cents independently, so allow 1c slack.
    assert c3 == pytest.approx(c1 * 1.5, abs=0.01)


def test_roll_incident_probabilistic():
    rng = np.random.default_rng(42)
    n = 5000
    fired = sum(1 for _ in range(n) if roll_incident(rng, "2026-04-24") is not None)
    # Calibrated to ~8% per day; allow generous tolerance.
    assert 0.05 < fired / n < 0.11


def test_sample_weather_returns_known_conditions():
    rng = np.random.default_rng(0)
    seen = {sample_weather(rng, dow=4)["condition"] for _ in range(500)}
    assert seen <= {"sunny", "cloudy", "rain", "storm"}
