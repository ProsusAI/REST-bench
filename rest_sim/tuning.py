"""Central tuning knobs for the simulation.

Every constant the day-sim or economy uses to *interpret* state lives
here, grouped by domain. Changing a value here should change behavior
predictably; the golden-run test catches accidental drift.

When mechanics change deliberately (Phase 2+), update both the constant
and the golden scorecard in one commit so reviewers can see the link.
"""
from __future__ import annotations


class FIXED_COSTS:
    RENT_PER_MONTH_EUR = 8000.0
    UTILITIES_PER_DAY_EUR = 60.0
    INSURANCE_PER_DAY_EUR = 10.0
    POS_PER_DAY_EUR = 5.0
    LOYALTY_PER_DAY_EUR = 50.0


class INVENTORY:
    DEFAULT_STOCK_PER_ITEM = 250
    SPOILAGE_RATE_PER_DAY = 0.04                 # 4% of perishable stock
    SUPPLIER_MARKUP = 1.15                        # restock cost = food_cost × this
    PERISHABLE_CATEGORIES = frozenset({"appetizer", "main", "side", "dessert"})


class SHOCK:
    DAILY_DRIFT_SIGMA = 0.01                      # log-space random walk
    SHOCK_PROBABILITY = 0.05
    SHOCK_MAGNITUDES = (-0.20, -0.15, -0.10, 0.10, 0.15, 0.20, 0.25)
    PRICE_MULT_MIN = 0.6
    PRICE_MULT_MAX = 2.0


class MARKETING:
    DIMINISHING_RETURNS_HALFLIFE_EUR = 400.0      # spend → arrival lift
    MAX_LIFT = 0.5                                # asymptote: 1 + MAX_LIFT
    LOYALTY_ARRIVAL_LIFT = 0.05                   # +5% when loyalty enabled


class REPUTATION:
    ANCHOR = 4.0                                  # neutral star score
    MULT_PER_STAR = 0.25                          # arrival multiplier slope
    MULT_FLOOR = 0.6
    MULT_CEIL = 1.4
    INITIAL = 4.2
    EWMA_REPUTATION_RETAIN = 0.90                 # weight on yesterday
    EWMA_SATISFACTION_RETAIN = 0.85               # weight on yesterday's EWMA
    IMPLIED_INTERCEPT = 2.0                       # implied = INTERCEPT + SLOPE × sat
    IMPLIED_SLOPE = 3.0
    MIN = 1.0
    MAX = 5.0


class SATISFACTION:
    INITIAL_EWMA = 0.80
    DEFAULT_DAY_VALUE_NO_VISITS = 0.80
    UNDERSTAFF_PENALTY_THRESHOLD = 0.8            # pressure below this → penalty
    UNDERSTAFF_PENALTY_SLOPE = 0.4
    OUTAGE_HIT_MULTIPLIER = 0.85                  # sat *= this on partial outage


class WAIT:
    REPUTATION_TOLERANCE_INTERCEPT = 0.7
    REPUTATION_TOLERANCE_SLOPE = 0.1              # × (rep − 3)
    REPUTATION_TOLERANCE_FLOOR = 0.5
    REPUTATION_TOLERANCE_CEIL = 1.3


class STAFF:
    EFFICIENCY_FLOOR = 0.4                        # individual server eff
    EFFICIENCY_CEIL = 1.5                         # pressure caps at this
    PRESSURE_WEIGHTS = {                          # role weights for staffing pressure
        "line_cook": 2.0, "sous_chef": 1.5, "chef": 1.0,
        "server": 2.0, "host": 1.0, "bartender": 1.0,
        "busser": 0.5, "dishwasher": 0.5, "manager": 0.3,
    }


class INCIDENT:
    DAILY_PROBABILITY = 0.08
    KINDS = (
        "oven_breakdown", "staff_sick", "bad_review",
        "noise_complaint", "health_inspection", "power_flicker",
    )
    EFFECTS = {
        "oven_breakdown":    {"kitchen_efficiency": 0.6, "cost": 350.0},
        "staff_sick":        {"staff_efficiency": 0.85, "cost": 0.0},
        "bad_review":        {"reputation_delta": -0.10, "cost": 0.0},
        "noise_complaint":   {"reputation_delta": -0.05, "cost": 150.0},
        "health_inspection": {"reputation_delta": 0.05, "cost": 0.0},
        "power_flicker":     {"kitchen_efficiency": 0.85, "cost": 80.0},
    }


class WEATHER:
    BASE_PROBS = {                                # by weekday/weekend
        "weekday": [0.30, 0.45, 0.20, 0.05],
        "weekend": [0.35, 0.45, 0.17, 0.03],
    }
    CONDITIONS = ("sunny", "cloudy", "rain", "storm")
    MULTIPLIERS = {"sunny": 1.08, "cloudy": 1.0, "rain": 0.85, "storm": 0.65}


class COMPETITOR:
    ARRIVAL_MULT_WHEN_ACTIVE = 0.90


class TABLE:
    CONVERSION_COST_EUR = 200.0


class COHORTS:
    """Phase 2.1 — slow-moving customer-population dynamics.

    The agent's choices today change the city's *opinion of the place*
    over weeks, not minutes. Three cohorts (regulars, occasionals,
    prospects) plus a `lost` bucket the agent can't recover from
    without sustained quality + marketing.
    """
    INITIAL = {"regulars": 80, "occasionals": 320, "prospects": 1500, "lost": 0}
    REGULAR_DEMAND_LIFT = 0.0008          # per regular, additive to multiplier
    OCCASIONAL_DEMAND_LIFT = 0.0002
    PROSPECT_DEMAND_LIFT = 0.00003
    LIFT_FLOOR = 0.7
    LIFT_CEIL = 1.4
    SAT_PROMOTE_THRESHOLD = 0.80          # daily mean above promotes
    SAT_DEMOTE_THRESHOLD = 0.65           # daily mean below demotes
    PROMOTION_RATE = 0.02                 # at full sat, 2% per day
    DEMOTION_RATE = 0.04                  # bad days bite harder than good days reward
    WALKOUT_TO_LOST_FRACTION = 0.15       # share of walkouts that demote a tier
    DAILY_NEW_PROSPECTS = 5               # baseline word-of-mouth replenishment
    MARKETING_PROSPECTS_PER_EUR = 0.05    # marketing buys back into the funnel


class REVIEW:
    """Phase 2.2 — delayed reviews drive reputation, not raw same-day sat."""
    POST_PROBABILITY_PER_VISIT = 0.20     # ~1 in 5 diners review
    LAG_GEOMETRIC_P = 0.40                # mean ~2.5 days
    LAG_MAX_DAYS = 14
    WALKOUT_GHOST_REVIEW_PROBABILITY = 0.05  # angry walkouts post 0-star
    GHOST_REVIEW_SAT = 0.0


class SUPPLIER_NEWS:
    """Phase 2.4 — probabilistic 1-3 day-ahead shock warnings."""
    DAILY_NEWS_PROBABILITY = 0.15
    LEAD_TIMES_DAYS = (1, 2, 3)


class SUBSTITUTION:
    """Phase 2.3 — outage substitutes degrade satisfaction but rescue revenue."""
    SAT_TAX = 0.10                        # sat *= (1 - tax) on substitution


class SCORE:
    """Phase-1 score model. Tuned so the no-action baseline is visibly
    negative (walkouts dominate) and the prior-best mutation is positive."""
    TARGET_SATISFACTION = 0.78
    TARGET_REPUTATION = 4.10
    SATISFACTION_PENALTY_SCALE_EUR = 200_000.0    # × (gap)² when below target
    REPUTATION_PENALTY_SCALE_EUR = 80_000.0       # × (gap)² when below target
    WALKOUT_PENALTY_PER_VISIT_EUR = 8.0
    BANKRUPTCY_THRESHOLD_EUR = -5_000.0
    BANKRUPT_SCORE_EUR = -150_000.0
