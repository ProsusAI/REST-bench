# Restaurant Sim — The Rotterdam Table

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)

A 30-day restaurant management simulation for **The Rotterdam Table**, a
22-table, 78-seat casual full-service bistro on Witte de Withstraat,
Rotterdam. Designed as a benchmark for LLM management agents: the agent
plays via a single-shot CLI (`python -m rest_sim <cmd>`), reading
status, making decisions, and advancing one day at a time.

## Score

```
score_eur = total_net_profit_eur
```

The agent's job is to maximise net profit over the month. Bankruptcy
(cash below −€5,000) halts the run early — the agent forfeits the
remaining days' earnings, but no extra penalty is applied.

## Multi-seed baseline (no decisions)

A no-action 30-day run across 8 seeds (seed = 20260423, 20260424,
20260801, 20261015, 20270101, 20270215, 20270601, 20270915):

| stat        | score_eur (= profit) |
|-------------|---------------------:|
| **mean**    | **−€5,577**          |
| median      | −€5,172              |
| stdev       | €1,573               |
| min         | −€8,303              |
| max         | −€3,972              |

A competent agent should clear positive profit by restocking, staffing
peaks, and capping reservations.

## Default agent run (`/play-month`)

Single 30-day run with default args (`--days 30 --seed 20260423 --cash
10000`), played by the `restaurant-manager` subagent:

| metric             | value         |
|--------------------|--------------:|
| **score_eur**      | **+€24,129**  |
| final cash         | €7,430        |
| mean satisfaction  | 0.75          |
| mean reputation    | 3.79          |
| total walkouts     | 2,885         |
| decisions          | 46            |

## Mechanics grounded in real statistics

Every customer-facing distribution is calibrated against published
service-industry research. Citations live inline in
`rest_sim/distributions.py`.

| Mechanic                  | Distribution / model              | Source                                             |
|---------------------------|-----------------------------------|----------------------------------------------------|
| **Customer arrivals**     | Non-homogeneous Poisson Process   | Kimes 1999; Thompson 2002; Tse & Poon 2017         |
| NHPP sampling             | Thinning algorithm                | Lewis & Shedler 1979                               |
| Hourly arrival shape      | Bimodal lunch/dinner peaks        | Toast peak-hour reports; OpenTable                 |
| **Party size**            | Empirical discrete (~45% pairs)   | OakStreet / Fast Casual / Toast operator data      |
| **Dining duration**       | Lognormal, CV = 0.30              | Kimes, Wirtz & Noone 2002; Thompson 2002           |
| **Kitchen prep time**     | Lognormal per category            | Brown et al. 2005; Gualandi & Toscani 2018         |
| **Menu popularity**       | Zipf, α = 1.16 (≈ 80/20)          | Pareto / Juran 1941                                |
| **Tip percentage**        | Gaussian mixture on 15/18/20/25%  | Toast Q1 2025 (19.4% avg); Pew anchor clustering   |
| **No-show rate**          | Weekday-dependent Bernoulli       | Tse & Poon 2017 (9–13%); OpenTable (15–20%)        |
| **Reservation lead time** | Mixture exponential (45% same-day)| Toast 2024                                         |
| **Wait tolerance**        | Exponential, mean ≈ 15 min        | Standard balking model                             |
| **Satisfaction**          | Beta(8, 2), degraded by waits     | Maister 1985 (first-wait dissatisfaction)          |
| **Staff efficiency**      | Truncated Normal, σ = 0.15        | Reported server-to-server variation                |

All RNG is seeded — `(seed, start_date)` produces an identical month.

## Files

### Static configuration (`data/`)
| file              | contents                                    |
|-------------------|---------------------------------------------|
| `restaurant.json` | identity, hours, tax rates                  |
| `tables.json`     | floor plan — 22 tables + 6 bar seats        |
| `menu.json`       | 42 items with prices, food cost, popularity |
| `staff.json`      | 18-person roster with roles, wages, shifts  |

### Source code (`rest_sim/`)
| file                    | contents                                                |
|-------------------------|---------------------------------------------------------|
| `tuning.py`             | central tuning knobs                                    |
| `distributions.py`      | RNG functions with inline citations                     |
| `config.py`             | menu / tables / staff definitions                       |
| `game_state.py`         | JSON-persisted state + reservation sidecar             |
| `economy.py`            | payroll, fixed costs, spoilage, shocks, marketing       |
| `cohorts.py`            | customer-cohort population dynamics                     |
| `reviews.py`            | delayed review queue → reputation                       |
| `day_sim.py`            | single-day simulator                                    |
| `observability.py`      | P&L, attribution, heatmap, scorecard, manager_view     |
| `dashboard.py`          | SSE backend serving live init/advance/decision events  |
| `dashboard_client.py`   | in-process publisher                                    |
| `dashboard_index.html`  | dashboard frontend                                      |
| `__main__.py`           | CLI subcommands                                         |

### Agent
| file                                   | contents                                  |
|----------------------------------------|-------------------------------------------|
| `.claude/agents/restaurant-manager.md` | Haiku-powered subagent that plays the sim |
| `.claude/commands/play-month.md`       | `/play-month` — reset and run the agent  |

## Running the simulation

```bash
python -m rest_sim init --days 30
```

Then either run the agent (`/play-month`) or play it yourself.

### Daily decision levers
- **Inventory** — `restock`, `inventory`
- **Menu** — `set-price`, `add-item`, `remove-item`, `menu`
- **Staffing** — `set-staff DATE ROLE COUNT`, `staffing`
- **Reservations** — `set-cap DATE CAP`, `reservations`, `reservations-next`
- **Marketing** — `marketing AMOUNT`, `loyalty {on|off}`, `promo CAT DISC`
- **Pricing windows** — `happy-hour --start --end --discount [--categories] [--days] [--from-date]`
- **Layout** — `tables`, `convert-table`
- **Information** — `status`, `kpis`, `pnl`, `attribution`, `heatmap`, `cohort`, `cohorts`, `news`, `decisions`

### Live dashboard

`python -m rest_sim dashboard [--port 8765]` serves an SSE-backed live
view. `init` auto-launches it unless `--no-dashboard-browser` is passed.

## What's modelled beyond the per-visit RNG

- **Customer cohorts** — three tiers (regulars, occasionals, prospects)
  plus a `lost` sink. Daily transitions driven by mean satisfaction and
  walkouts. Cohort populations contribute a slow-moving demand multiplier
  on top of reputation, marketing, and weather.
- **Delayed reviews** — visits leave reviews probabilistically with a
  geometric lag (mean ~2.5 days, capped 14). Walkouts can post 0-star
  ghost reviews. Reputation EWMAs over today's *posted* reviews, not
  today's diners — so a bad day bleeds for a week.
- **Substitution** — when a category runs out, orders fall back to a
  sibling category (main → appetizer, side → appetizer, dessert → drink)
  with a satisfaction tax instead of an automatic walkout.
- **Supplier news** — 1–3 day-ahead probabilistic alerts about upcoming
  ingredient shocks, surfaced via `news` and in `status`. Shocks are
  scheduled by news, not surprise-rolled.
- **Bankruptcy** — cash below −€5,000 halts the run.
- **Partial observability** — `status` returns a `manager_view`: last 7
  days of KPIs, banded cohort sizes, supplier news, reviews-posted-today.
  Engine internals (raw cohort counts, future reviews queue, full
  history) are hidden. The unmasked view is `--full` (forbidden to the
  agent).

## Anti-cheat

Pre-generated reservations live in `game/reservations.json` (sidecar),
not in the agent-readable `state.json`. The agent must use the
`reservations` / `reservations-next` CLI subcommands. `status --full` is
reserved for tests and the dashboard.

## Testing

```bash
pytest -q
```

47 tests across:
- `test_golden_run.py` — deterministic 30-day no-decision regression
- `test_economy.py` — pure economy functions
- `test_distributions.py` — sampling shape & moments
- `test_score.py` — profit-only score + bankruptcy
- `test_phase2.py` — cohorts + reviews + supplier news
- `test_phase4.py` — manager view masking

## License

REST-bench is licensed under the [Apache License 2.0](LICENSE). Copyright © 2026 MIH AI B.V.
See [NOTICE](NOTICE) for third-party attributions.
