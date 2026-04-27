# Restaurant Sim — The Rotterdam Table

A 30-day interactive restaurant management simulation for **The Rotterdam Table**, a
22-table, 78-seat casual full-service bistro on Witte de Withstraat, Rotterdam.

The simulation is designed as a benchmark for LLM management agents: the agent
plays via a single-shot CLI (`python -m rest_sim <cmd>`), reading status,
making decisions, and advancing day by day. An autoresearch ratchet
(`.claude/commands/auto-research.md`) mutates the agent's prompt and keeps
mutations that improve the score on a fixed seed *and* a held-out validation seed.

## Files

### Static configuration
| file              | contents                                       |
|-------------------|------------------------------------------------|
| `restaurant.json` | identity, hours, tax rates                     |
| `tables.json`     | floor plan — 22 tables + 6 bar seats           |
| `menu.json`       | 42 items with prices, food cost, popularity    |
| `staff.json`      | 18-person roster with roles, wages, shifts     |

### Source code
| file                 | contents                                          |
|----------------------|---------------------------------------------------|
| `rest_sim/tuning.py` | central tuning knobs — every magic number        |
| `distributions.py`   | RNG functions with inline citations               |
| `config.py`          | menu / tables / staff definitions                 |
| `game_state.py`      | JSON-persisted state + reservation sidecar       |
| `economy.py`         | payroll, fixed costs, spoilage, scheduled shocks, marketing |
| `cohorts.py`         | customer-cohort population dynamics              |
| `reviews.py`         | delayed review queue → reputation                |
| `day_sim.py`         | single-day simulator                              |
| `observability.py`   | P&L, attribution, heatmap, scorecard, manager_view |
| `dashboard.py`       | SSE backend serving live init/advance/decision events |
| `dashboard_client.py`| in-process publisher that posts events to the dashboard |
| `dashboard_index.html` | dashboard frontend (live tiles, KPI charts)     |
| `__main__.py`        | CLI subcommands                                   |

### Agent & commands
| file                                       | contents                                     |
|--------------------------------------------|----------------------------------------------|
| `.claude/agents/restaurant-manager.md`     | Haiku-powered subagent that plays the sim    |
| `.claude/commands/play-month.md`           | `/play-month` — reset and run the agent      |
| `.claude/commands/auto-research.md`        | `/auto-research` — dual-seed prompt ratchet  |

## Running the simulation

```
python -m rest_sim init --days 30
```

Then invoke the subagent:

> "Run the restaurant-manager agent to play the full month."

## Mechanics summary

The simulation is research-grounded: NHPP arrivals (Kimes 1999, Thompson 2002),
lognormal dining durations (Thompson 2002 CV ≈ 0.30), Zipf menu popularity
(α = 1.16), Beta satisfaction with wait penalties (Maister 1985), and tip
anchoring (Toast Q1 2025). All RNG is seeded so a given `(seed, start_date)`
produces an identical month.

### Daily decision levers
- **Inventory**: `restock`, `inventory`
- **Menu**: `set-price`, `add-item`, `remove-item`, `menu`
- **Staffing**: `set-staff DATE ROLE COUNT`, `staffing`
- **Reservations**: `set-cap DATE CAP`, `reservations`, `reservations-next`
- **Marketing**: `marketing AMOUNT`, `loyalty {on|off}`, `promo CAT DISC`
- **Pricing windows** (Phase 3.1): `happy-hour --start --end --discount [--categories] [--days] [--from-date]`
- **Layout**: `tables`, `convert-table`
- **Information**: `status`, `kpis`, `pnl`, `attribution`, `heatmap`, `cohort`, `cohorts`, `news`, `decisions`

### Live dashboard

`python -m rest_sim dashboard [--port 8765]` serves an SSE-backed live view
(`init` / `advance` / `decision` events). `init` auto-launches it unless
`--no-dashboard-browser` is passed. Backed by `dashboard.py` (server) and
`dashboard_client.py` (in-process publisher) — frontend in
`dashboard_index.html`.

### Score (Phase 1.1)
```
score_eur = total_net_profit_eur
          − 200_000 × max(0, 0.78 − mean_satisfaction)²
          −  80_000 × max(0, 4.10 − mean_reputation)²
          −       8 × total_walkouts
          (or BANKRUPT_SCORE_EUR = -150_000 if cash dipped below -€5,000)
```

Walkouts now have an explicit cost (they were ignored before). The previous
cliff penalties (`max(0, 0.75 − sat) × 100k`) are replaced with quadratic
penalties — same gradient, no catastrophic-cliff incentives.

### What's modeled

- **Customer cohorts** (Phase 2.1) — three tiers (regulars, occasionals,
  prospects) plus a `lost` sink. Daily transitions driven by mean
  satisfaction and walkouts. Cohort populations contribute a slow-moving
  demand multiplier on top of reputation/marketing/weather.
- **Delayed reviews** (Phase 2.2) — visits leave reviews probabilistically with
  a geometric lag (mean ~2.5 days, capped 14). Walkouts can post 0-star
  ghost reviews. Reputation EWMAs over today's *posted reviews*, not today's
  diners — so a bad day bleeds for a week.
- **Substitution** (Phase 2.3) — when a category runs out, orders fall back
  to a sibling category (main → appetizer, side → appetizer, dessert →
  drink) with a satisfaction tax instead of an automatic walkout.
- **Supplier news** (Phase 2.4) — 1–3 day-ahead probabilistic alerts about
  upcoming ingredient shocks, surfaced via `news` and in `status`.
  Shocks are now scheduled by news, not surprise-rolled.
- **Bankruptcy** (Phase 1.2) — cash below −€5,000 halts the run and floors
  the score.
- **Held-out validation seed** (Phase 1.3) — autoresearch ratchet runs both
  seed A=20260424 and seed B=20260801; mutations are kept only if they improve
  on A *and* don't regress on B by > 5%.
- **Structured decision-outcome log** (Phase 1.4) — `game/decisions_log.jsonl`
  links every decision to a pre-state digest and the next day's KPI.
- **Partial observability** (Phase 4.1) — `status` returns a `manager_view`:
  last 7 days of KPIs, banded cohort sizes, supplier news, reviews-posted-today.
  Engine internals (raw cohort counts, future reviews queue, full history)
  are hidden. The unmasked view is `--full` (forbidden to the agent).

### Anti-cheat

Pre-generated reservations are stored in `game/reservations.json` (sidecar),
not in the agent-readable `state.json`. The agent must use the
`reservations` / `reservations-next` CLI subcommands.

Partial observability (Phase 4.1) is enforced at the CLI: `status` returns
the masked `manager_view`. The unmasked engine state is only available via
`status --full`, which is reserved for tests and the dashboard — agents
must not call it.

## No-action 30-day baseline (Phase-2 mechanics)

| metric | value |
|---|---|
| total revenue | €101,597 |
| net profit | −€3,972 |
| mean satisfaction | 0.749 |
| mean reputation | 3.543 |
| walkouts | 3,636 |
| **score_eur** | **−€58,085** |

The score is dominated by the walkout penalty (−€29,088) and reputation
penalty (−€24,829). A competent agent should clear positive score by
restocking, staffing peaks, and capping reservations. Mean reputation
above 4.10 fully removes the reputation penalty.

## Testing

```bash
pytest -q
```

49 tests across:
- `test_golden_run.py` — deterministic 30-day no-decision regression
- `test_economy.py` — pure economy functions
- `test_distributions.py` — sampling shape & moments
- `test_score.py` — Phase-1 score formula + bankruptcy
- `test_phase2.py` — cohorts + reviews + supplier news
- `test_phase4.py` — manager view masking

## Deferred items

The full plan included 15 items; the following were intentionally deferred
after the user accepted "Phase 0–2 + 3.1 + 4.1 ships, the rest later" given
the diminishing return relative to the items already in. Each is a clean
extension point in the codebase:

- **2.5 Smarter seating with waitlist** — current first-fit by smallest-fit
  table works; adding a true FIFO waitlist + table-quality scoring is a
  ~50-line `day_sim` change.
- **3.2 Marketing channel mix** — replace scalar `marketing_spend_by_day`
  with `{social, press, flyer, influencer}` channels with cohort-specific
  lifts. Cohorts are already in place to receive this.
- **3.3 Supplier choice & contracts** — 3 named suppliers with
  markup/quality/reliability tradeoffs and weekly contracts. `restock_cost`
  in `economy.py` is the integration point.
- **3.4 Per-shift staff scheduling** — replace `staffing_override[date][role]`
  with `staffing_override[date][role][shift]` (open/lunch/dinner/close) +
  overtime cost. Touches `payroll_for_date` + `staffing_pressure`.
- **4.2 Dashboard frontend completion** — backend SSE pipeline is wired
  (Phase 0.3 made errors visible). Frontend tiles need finishing.

## Versioning

- `autoresearch/results_v0.tsv` — frozen results from the pre-Phase-1 metric.
- `autoresearch/results.tsv` (created on first ratchet run) — current
  metric (Phase 1+, dual-seed validation).
