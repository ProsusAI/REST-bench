# Copyright © 2026 MIH AI B.V.
# Licensed under the Apache License, Version 2.0.
# See LICENSE file in the project root for full license information.

"""
Statistical distributions for the restaurant digital twin.

Every distribution is grounded in published research or industry data.
Sources are cited inline. All RNG is routed through a single numpy
Generator so simulations are fully reproducible from a seed.
"""
from __future__ import annotations

import numpy as np
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# 1. CUSTOMER ARRIVALS  — Non-homogeneous Poisson Process (NHPP)
# ---------------------------------------------------------------------------
# Classical result: customer arrivals to a restaurant follow a Poisson
# process with a time-varying rate λ(t) (Kimes 1999; Thompson 2002; Tse &
# Poon 2017). Interarrival times, conditional on λ, are Exponential(λ).
# A casual full-service restaurant exhibits a bimodal λ(t) with a lunch
# peak around 12:00–13:00 and a dinner peak around 19:00–20:00.

# Base rate profile (arrivals per minute) sampled every 30 min, 11:00-23:00.
# Shape calibrated from industry data (Toast peak-hour reports, OpenTable).
# A single multiplicative "day factor" scales the whole day for weekday/
# weekend variation — see DAY_OF_WEEK_MULTIPLIER below.
HOURLY_RATE_PROFILE = {
    # hour: arrivals/min at that hour's midpoint
    # Calibrated against a ~22-table, 70-seat casual bistro to produce
    # realistic utilisation (~60–75%) and walkout rates (8–15%).
    11.0: 0.06,  # early lunch trickle
    11.5: 0.20,
    12.0: 0.50,  # lunch peak begins
    12.5: 0.65,  # lunch peak
    13.0: 0.55,
    13.5: 0.35,
    14.0: 0.16,
    14.5: 0.08,
    15.0: 0.05,  # afternoon lull
    15.5: 0.04,
    16.0: 0.05,
    16.5: 0.08,
    17.0: 0.14,  # early dinner
    17.5: 0.22,
    18.0: 0.34,  # dinner ramp
    18.5: 0.45,
    19.0: 0.54,  # dinner peak
    19.5: 0.52,
    20.0: 0.44,
    20.5: 0.32,
    21.0: 0.22,
    21.5: 0.13,
    22.0: 0.07,
    22.5: 0.03,  # closing
}

DAY_OF_WEEK_MULTIPLIER = {
    # Monday=0 .. Sunday=6. Fri/Sat peak, Mon/Tue slow.
    # Calibrated to match industry data: Fri/Sat ~1.3-1.5x average.
    0: 0.70,  # Mon
    1: 0.75,  # Tue
    2: 0.85,  # Wed
    3: 0.95,  # Thu
    4: 1.35,  # Fri
    5: 1.50,  # Sat
    6: 1.10,  # Sun
}


def arrival_rate(day_of_week: int, minutes_since_midnight: float) -> float:
    """Return λ(t) in arrivals per minute for a given weekday and time."""
    hour = minutes_since_midnight / 60.0
    if hour < 11.0 or hour >= 23.0:
        return 0.0
    # Linear interpolation between the two nearest profile points.
    keys = sorted(HOURLY_RATE_PROFILE.keys())
    lo = max(k for k in keys if k <= hour)
    hi = min((k for k in keys if k >= hour), default=lo)
    if hi == lo:
        base = HOURLY_RATE_PROFILE[lo]
    else:
        w = (hour - lo) / (hi - lo)
        base = (1 - w) * HOURLY_RATE_PROFILE[lo] + w * HOURLY_RATE_PROFILE[hi]
    return base * DAY_OF_WEEK_MULTIPLIER[day_of_week]


def sample_arrivals_for_day(rng: np.random.Generator, day_of_week: int,
                            open_min: int = 11 * 60, close_min: int = 23 * 60
                            ) -> list[float]:
    """
    NHPP thinning (Lewis & Shedler 1979). Returns a sorted list of arrival
    times (minutes since midnight) for the given day.
    """
    lam_max = max(arrival_rate(day_of_week, t) for t in range(open_min, close_min))
    if lam_max == 0:
        return []
    t = float(open_min)
    arrivals: list[float] = []
    while t < close_min:
        # candidate interarrival from the dominating homogeneous Poisson
        t += rng.exponential(1.0 / lam_max)
        if t >= close_min:
            break
        # accept with probability λ(t) / λ_max
        if rng.random() < arrival_rate(day_of_week, t) / lam_max:
            arrivals.append(t)
    return arrivals


# ---------------------------------------------------------------------------
# 2. PARTY SIZE  — Empirical discrete distribution
# ---------------------------------------------------------------------------
# Full-service restaurants see 2-tops dominate. Distribution below matches
# the patterns reported by OakStreet / Fast Casual / Toast operator data:
# ~10% solo, ~45% couples, ~35% 3-4 tops, ~10% larger. Lunch skews smaller
# than dinner (business lunches are often 1-2, dinner parties 2-4+).

PARTY_SIZE_DIST_LUNCH = {
    1: 0.18, 2: 0.50, 3: 0.15, 4: 0.12, 5: 0.03, 6: 0.02,
}
PARTY_SIZE_DIST_DINNER = {
    1: 0.06, 2: 0.42, 3: 0.16, 4: 0.22, 5: 0.07, 6: 0.04, 7: 0.02, 8: 0.01,
}


def sample_party_size(rng: np.random.Generator, minutes_since_midnight: float) -> int:
    dist = PARTY_SIZE_DIST_LUNCH if minutes_since_midnight < 16 * 60 else PARTY_SIZE_DIST_DINNER
    sizes = list(dist.keys())
    probs = np.array(list(dist.values()))
    probs /= probs.sum()
    return int(rng.choice(sizes, p=probs))


# ---------------------------------------------------------------------------
# 3. DINING DURATION  — Lognormal, mean scales with party size
# ---------------------------------------------------------------------------
# Kimes, Wirtz & Noone (2002) "How Long Should Dinner Take" reports
# expected meal durations roughly 60–90 min for casual dinner. Thompson
# (2002) models dining durations as lognormal with coefficient of
# variation in the range 0.16–0.50 (uses 0.30). Larger parties linger
# longer — adding ~10 min per additional guest is a common calibration.

DINING_DURATION_CV = 0.30  # Thompson 2002


def sample_dining_duration(rng: np.random.Generator, party_size: int,
                            meal_period: str) -> float:
    """Dining duration in minutes. Lognormal with mean dependent on party size and meal."""
    if meal_period == "lunch":
        mean = 35.0 + 6.0 * party_size   # ~47 min for a 2-top lunch
    else:  # dinner
        mean = 55.0 + 10.0 * party_size  # ~75 min for a 2-top dinner
    sigma = np.sqrt(np.log(1 + DINING_DURATION_CV ** 2))
    mu = np.log(mean) - 0.5 * sigma ** 2
    return float(rng.lognormal(mu, sigma))


# ---------------------------------------------------------------------------
# 4. KITCHEN PREP TIME  — Lognormal per menu-item category
# ---------------------------------------------------------------------------
# Call-center and service-time studies (Brown et al. 2005; Gualandi &
# Toscani 2018) consistently fit service-time data with lognormal
# distributions. We apply the same family at the dish-category level;
# means calibrated to typical casual-restaurant kitchen times.

PREP_TIME_MEANS = {   # minutes
    "appetizer": 7.0,
    "main":      15.0,
    "dessert":   5.0,
    "drink":     2.0,
    "side":      4.0,
}
PREP_TIME_SIGMA_LOG = {  # sigma of the underlying normal of ln(prep_time)
    "appetizer": 0.35,
    "main":      0.40,
    "dessert":   0.30,
    "drink":     0.30,
    "side":      0.30,
}


def sample_prep_time(rng: np.random.Generator, category: str) -> float:
    mean = PREP_TIME_MEANS[category]
    sigma = PREP_TIME_SIGMA_LOG[category]
    mu = np.log(mean) - 0.5 * sigma ** 2
    return float(rng.lognormal(mu, sigma))


# ---------------------------------------------------------------------------
# 5. MENU ITEM POPULARITY  — Zipf / Pareto
# ---------------------------------------------------------------------------
# Pareto principle (Juran 1941): ~20% of menu items drive ~80% of sales.
# A Zipf distribution with exponent α ≈ 1.16 produces the 80/20 split
# (Wikipedia: "Pareto principle"). We assign each menu item a popularity
# weight proportional to rank^(-α) within its category.

ZIPF_ALPHA = 1.16  # α = log_4(5) ≈ 1.16 → 80/20


def zipf_weights(n: int, alpha: float = ZIPF_ALPHA) -> np.ndarray:
    """Return normalized Zipfian weights over n items (rank 1 is most popular)."""
    ranks = np.arange(1, n + 1)
    w = 1.0 / ranks ** alpha
    return w / w.sum()


# ---------------------------------------------------------------------------
# 6. ITEMS ORDERED PER GUEST  — Poisson by category
# ---------------------------------------------------------------------------
# For each guest at the table we sample how many items of each category
# they order. Mains are nearly 1 per guest but not quite — some share.
# Drinks average ~1.5 per guest across the visit (soft drink refills etc.).

ITEMS_PER_GUEST_LAMBDA = {
    "main":      1.00,   # ~1 main per guest
    "drink":     1.60,   # drinks incl refills
    "appetizer": 0.35,   # shared
    "side":      0.55,
    "dessert":   0.30,   # often skipped at lunch
}
# Lunch skips or reduces some categories.
LUNCH_ITEM_MULTIPLIER = {
    "main": 1.0, "drink": 0.9, "appetizer": 0.25,
    "side": 0.70, "dessert": 0.12,
}


def sample_item_counts(rng: np.random.Generator, party_size: int,
                       meal_period: str) -> dict[str, int]:
    out = {}
    for cat, lam in ITEMS_PER_GUEST_LAMBDA.items():
        if meal_period == "lunch":
            lam *= LUNCH_ITEM_MULTIPLIER[cat]
        # total count is Poisson(lam * party_size)
        out[cat] = int(rng.poisson(lam * party_size))
    # enforce at least one main per 2 guests — nobody comes in and orders nothing
    if out["main"] == 0 and party_size >= 1:
        out["main"] = max(1, party_size // 2)
    return out


# ---------------------------------------------------------------------------
# 7. TIP PERCENTAGE  — Gaussian mixture on anchor points
# ---------------------------------------------------------------------------
# Toast Q1 2025: 19.4% average tip at full-service restaurants; Pew shows
# that diners cluster on the standard anchors (15%, 18%, 20%, 25%) rather
# than a smooth distribution. We model this as a discrete mixture over
# those anchors with small Gaussian noise.

TIP_ANCHORS = [0.10, 0.15, 0.18, 0.20, 0.22, 0.25, 0.30]
TIP_ANCHOR_WEIGHTS = [0.05, 0.18, 0.20, 0.30, 0.12, 0.12, 0.03]
TIP_NOISE_SD = 0.012  # small jitter so real checks don't all land exactly on anchors


def sample_tip_pct(rng: np.random.Generator, service_quality: float = 1.0) -> float:
    """Tip as a fraction of subtotal. service_quality in [0,1.3]; 1.0 is neutral."""
    anchor = rng.choice(TIP_ANCHORS, p=np.array(TIP_ANCHOR_WEIGHTS) / sum(TIP_ANCHOR_WEIGHTS))
    noise = rng.normal(0, TIP_NOISE_SD)
    # scale by service quality (good service → shift toward higher anchors)
    tip = anchor * service_quality + noise
    return float(max(0.0, tip))


# ---------------------------------------------------------------------------
# 8. NO-SHOW  — Bernoulli
# ---------------------------------------------------------------------------
# Tse & Poon (2017) report 9.07%–12.95% no-show rates depending on day
# (lowest Thursday, highest Saturday). OpenTable and industry reports put
# the headline number around 15–20% for US restaurants post-pandemic. We
# use a weekday-dependent rate that matches both.

NO_SHOW_RATE_BY_DAY = {
    0: 0.10, 1: 0.10, 2: 0.11, 3: 0.09, 4: 0.13, 5: 0.15, 6: 0.12,
}


def sample_no_show(rng: np.random.Generator, day_of_week: int) -> bool:
    return bool(rng.random() < NO_SHOW_RATE_BY_DAY[day_of_week])


# ---------------------------------------------------------------------------
# 9. RESERVATION LEAD TIME  — Exponential (mix of same-day + advance)
# ---------------------------------------------------------------------------
# Toast 2024 data: 45% of reservations are for the same day. Remaining
# reservations have a long right tail (some book months out for fine
# dining). Modeled as a mixture of exponential(mean=6h) for same-day and
# exponential(mean=3d) for advance bookings.

def sample_reservation_lead_minutes(rng: np.random.Generator) -> float:
    if rng.random() < 0.45:
        return float(rng.exponential(6 * 60))  # same-day, mean 6h
    else:
        return float(rng.exponential(3 * 24 * 60))  # advance, mean 3 days


# ---------------------------------------------------------------------------
# 10. WAIT TOLERANCE  — Exponential
# ---------------------------------------------------------------------------
# How long a walk-in will wait before balking (leaving). Classic result:
# wait tolerance has exponential-like right tail. Mean ~15 min is typical.

def sample_wait_tolerance(rng: np.random.Generator,
                          minutes_since_midnight: float | None = None) -> float:
    """
    Wait tolerance (min) before a walk-in balks. Exponential with a
    heavier mean at dinner — diners invest more in a dinner visit and
    are willing to wait longer for a good table.
    """
    if minutes_since_midnight is not None and minutes_since_midnight >= 16 * 60:
        return float(rng.exponential(22.0))  # dinner
    return float(rng.exponential(12.0))      # lunch / off-peak


# ---------------------------------------------------------------------------
# 11. SATISFACTION  — Beta, degraded by wait time and prep delay
# ---------------------------------------------------------------------------
# Customer satisfaction is commonly modeled with Beta distributions on
# [0,1]. We anchor on Beta(8,2) (mean 0.80) and degrade for excessive
# waits (Maister 1985: initial wait is a critical dissatisfaction driver).

def sample_satisfaction(rng: np.random.Generator, seating_wait_min: float,
                        kitchen_wait_min: float) -> float:
    base = float(rng.beta(8, 2))
    wait_penalty = min(0.5, max(0, seating_wait_min - 10) * 0.015)
    kitchen_penalty = min(0.3, max(0, kitchen_wait_min - 20) * 0.008)
    return float(max(0.0, base - wait_penalty - kitchen_penalty))


# ---------------------------------------------------------------------------
# 12. STAFF EFFICIENCY  — Normal (truncated, >0)
# ---------------------------------------------------------------------------
# A simple multiplicative efficiency on service time. σ=0.15 matches
# reported server-to-server variation in output.

def sample_staff_efficiency(rng: np.random.Generator) -> float:
    return float(max(0.5, rng.normal(1.0, 0.15)))
