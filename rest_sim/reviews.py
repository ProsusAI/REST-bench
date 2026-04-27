"""Delayed review queue — Phase 2.2.

Replaces the old direct EWMA on raw same-day satisfaction with a
review-posting model: each visit has a ~20% chance of leaving a review
with a geometric lag (mean ~2.5 days, capped at 14). Walkouts can post
0-star ghost reviews. Reputation EWMAs over today's *posted reviews*,
not today's diners — so a bad day bleeds for a week, and recovery
requires sustained quality.
"""
from __future__ import annotations

import numpy as np

from .economy import update_reputation
from .tuning import REPUTATION, REVIEW


def queue_review(state: dict, day_index: int, satisfaction: float,
                 rng: np.random.Generator) -> None:
    """Probabilistically queue a single visit's review."""
    if rng.random() >= REVIEW.POST_PROBABILITY_PER_VISIT:
        return
    lag = 1 + int(rng.geometric(REVIEW.LAG_GEOMETRIC_P) - 1)
    lag = min(REVIEW.LAG_MAX_DAYS, max(0, lag))
    state.setdefault("review_queue", []).append({
        "post_on_day_index": day_index + lag,
        "satisfaction": round(float(satisfaction), 4),
    })


def queue_ghost_walkout_review(state: dict, day_index: int,
                               rng: np.random.Generator) -> None:
    """A small fraction of walkouts post 0-star ghost reviews."""
    if rng.random() >= REVIEW.WALKOUT_GHOST_REVIEW_PROBABILITY:
        return
    lag = 1 + int(rng.geometric(REVIEW.LAG_GEOMETRIC_P) - 1)
    lag = min(REVIEW.LAG_MAX_DAYS, max(0, lag))
    state.setdefault("review_queue", []).append({
        "post_on_day_index": day_index + lag,
        "satisfaction": REVIEW.GHOST_REVIEW_SAT,
    })


def post_due_reviews(state: dict, day_index: int) -> tuple[list[float], int]:
    """Pull all reviews due on or before `day_index`, drive reputation
    EWMA, and remove them from the queue.

    Returns (posted_satisfactions, count) — the count is exposed to
    the manager-view as 'reviews_posted_today'.
    """
    queue = state.get("review_queue", [])
    if not queue:
        return [], 0
    due = [r for r in queue if r["post_on_day_index"] <= day_index]
    state["review_queue"] = [r for r in queue if r["post_on_day_index"] > day_index]
    if not due:
        return [], 0
    sats = [float(r["satisfaction"]) for r in due]
    mean_sat = float(np.mean(sats))
    update_reputation(state, mean_sat)
    return sats, len(sats)
