# Copyright © 2026 MIH AI B.V.
# Licensed under the Apache License, Version 2.0.
# See LICENSE file in the project root for full license information.

"""Sanity-check tests for distribution sampling shape & moments."""
from __future__ import annotations

import numpy as np
import pytest

from rest_sim import distributions as dists


def test_arrival_rate_zero_outside_hours():
    assert dists.arrival_rate(4, 5 * 60) == 0
    assert dists.arrival_rate(4, 23.5 * 60) == 0


def test_arrival_rate_higher_on_weekend():
    sat = dists.arrival_rate(5, 13 * 60)
    mon = dists.arrival_rate(0, 13 * 60)
    assert sat > mon


def test_sample_arrivals_for_day_returns_sorted_in_range():
    rng = np.random.default_rng(0)
    arrivals = dists.sample_arrivals_for_day(rng, day_of_week=5)
    assert arrivals == sorted(arrivals)
    assert all(11 * 60 <= t < 23 * 60 for t in arrivals)


def test_party_size_distribution_dominated_by_2():
    rng = np.random.default_rng(0)
    sizes = [dists.sample_party_size(rng, 19 * 60) for _ in range(5000)]
    # 2-tops should be the modal party size at dinner.
    most_common = max(set(sizes), key=sizes.count)
    assert most_common == 2


def test_dining_duration_dinner_longer_than_lunch():
    rng = np.random.default_rng(0)
    lunches = [dists.sample_dining_duration(rng, 2, "lunch") for _ in range(500)]
    dinners = [dists.sample_dining_duration(rng, 2, "dinner") for _ in range(500)]
    assert np.mean(dinners) > np.mean(lunches)


def test_prep_time_main_longer_than_drink():
    rng = np.random.default_rng(0)
    mains = [dists.sample_prep_time(rng, "main") for _ in range(500)]
    drinks = [dists.sample_prep_time(rng, "drink") for _ in range(500)]
    assert np.mean(mains) > np.mean(drinks)


def test_zipf_weights_normalize_to_one():
    w = dists.zipf_weights(20)
    assert w.sum() == pytest.approx(1.0)
    # First item is dominant under α=1.16
    assert w[0] > 5 * w[-1]


def test_no_show_rate_in_plausible_range():
    rng = np.random.default_rng(0)
    n = 5000
    fri_rate = sum(dists.sample_no_show(rng, 4) for _ in range(n)) / n
    assert 0.10 < fri_rate < 0.18  # calibrated 0.13


def test_sample_satisfaction_degraded_by_long_wait():
    rng = np.random.default_rng(0)
    fast = np.mean([dists.sample_satisfaction(rng, 0, 0) for _ in range(500)])
    slow = np.mean([dists.sample_satisfaction(rng, 30, 0) for _ in range(500)])
    assert fast > slow


def test_sample_tip_pct_higher_with_better_service():
    rng = np.random.default_rng(0)
    bad = np.mean([dists.sample_tip_pct(rng, service_quality=0.7) for _ in range(2000)])
    good = np.mean([dists.sample_tip_pct(rng, service_quality=1.2) for _ in range(2000)])
    assert good > bad
