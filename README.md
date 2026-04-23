# Restaurant Sim — The Rotterdam Table

A 30-day interactive restaurant management simulation for **The Rotterdam Table**, a
22-table, 78-seat casual full-service bistro on Witte de Withstraat, Rotterdam.

## Files

### Static configuration
| file              | contents                                       |
|-------------------|------------------------------------------------|
| `restaurant.json` | identity, hours, tax rates                     |
| `tables.json`     | floor plan — 22 tables + 6 bar seats           |
| `menu.json`       | 42 items with prices, food cost, popularity    |
| `staff.json`      | 18-person roster with roles, wages, shifts     |

### Source code
| file              | contents                                          |
|-------------------|---------------------------------------------------|
| `distributions.py`| all RNG functions with inline citations           |
| `config.py`       | menu / tables / staff definitions                 |
| `game_state.py`   | JSON-persisted state (cash, inventory, prices, promos, staffing, caps, reputation, weather, incidents…) |
| `economy.py`      | payroll, fixed costs, spoilage, ingredient-price shocks, marketing lift, reputation→demand |
| `day_sim.py`      | single-day simulator reading all state levers     |
| `observability.py`| P&L, attribution (last-7 vs prior-7), hour×dow heatmap, cohort, status snapshot, scorecard |
| `rest_sim/__main__.py`     | 25 subcommands, each one-shot                     |

### Agent
| file                                   | contents                   |
|----------------------------------------|----------------------------|
| `.claude/agents/restaurant-manager.md` | Haiku-powered subagent     |

## Running the simulation

```
python -m rest_sim init --days 30
```

Then invoke the subagent:

> "Run the restaurant-manager agent to play the full month."

#### Verified baseline (agent does nothing)

| day | event |
|----:|-------|
| 1   | €10.6k revenue, profitable |
| 6   | inventory depletes → walkouts spike |
| 30  | revenue collapses to ~0, net loss €5.8k, 3,565 walkouts |

A competent manager should clear **€10–15k net profit** by restocking, staffing Fri/Sat, capping reservations, and nudging prices when ingredients spike.

## Agent performance (30-day runs)

| metric | Haiku 4.5 |
|---|---|
| net profit | €116,267 |
| total revenue | €276,883 |
| gross margin | 69.7% |
| cash delta | +€15,523 |
| mean satisfaction | 0.687 |
| mean reputation | 4.094 |
| walkouts | 1,490 |
| total tokens | 27,610 |
| tool uses | 38 |
| duration | 2m 13s |
