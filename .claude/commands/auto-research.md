---
description: AutoResearch ratchet — mutate restaurant-manager.md, run /play-month against TWO seeds, record metric, keep winners that don't regress on the held-out seed, NEVER STOP.
argument-hint: "[max_iters]"
allowed-tools: Bash, Read, Edit, Write
---

You are running an **AutoResearch ratchet** (Karpathy-style) on The Rotterdam Table restaurant sim with a held-out validation seed (Phase 1.3).

**Editable artifact (the only file you may change):** `.claude/agents/restaurant-manager.md`
**Read-only harness:** everything else (the `rest_sim` package, `data/`, `game/`, `/play-month`).

**Seeds:**
- Train (`A`): `20260424` — start date `2026-04-24`
- Validation (`B`): `20260801` — start date `2026-08-01`

A mutation is kept iff it improves on `A` **and** does not regress on `B` by more than 5%. This guards against overfitting to a single month.

**Metric (higher is better)** — read directly from the scorecard's `score_eur`:

```
score_eur = total_net_profit_eur
          - α × max(0, 0.78 - mean_satisfaction)² × 200_000
          - β × max(0, 4.10 - mean_reputation)²   ×  80_000
          - 8 × total_walkouts
          (or BANKRUPT_SCORE_EUR = -150_000 if cash dipped below -€5,000)
```

Tunings live in `rest_sim/tuning.py:SCORE` — do not change them.

**Budget:** `$1` iterations (default 20). If omitted, run until told to stop.

---

## Setup (run once at the top of the command)

1. Verify branch: `git rev-parse --abbrev-ref HEAD` — must start with `autoresearch/restaurant-`. If not, create one: `git checkout -b autoresearch/restaurant-$(date +%Y%m%d-%H%M%S)`.
2. If `results.tsv` does not exist, create it with the header row:

   ```
   iter	commit	score_A	score_B	profit_A	profit_B	sat_A	sat_B	rep_A	rep_B	walk_A	walk_B	status	description
   ```

   Tab-separated. Do **not** commit `results.tsv` (it's untracked, per ratchet convention).
3. Read the last ~20 rows of `results.tsv` to understand what's already been tried. Read the current `.claude/agents/restaurant-manager.md` to know the baseline. Read the last 3 commits (`git log --oneline -10`) for context on past experiments.

If there is no baseline score yet (fresh `results.tsv`), the first iteration's job is to establish one by running `/play-month` against the unmodified agent on **both seeds**.

---

## The ratchet loop (repeat until iter budget exhausted — NEVER STOP otherwise)

For each iteration `N`:

### 1. Pick one experimental idea.
Pick ONE concrete, testable change to `.claude/agents/restaurant-manager.md`. Good targets:
- Staffing day-of-week multipliers
- Reservation cap thresholds
- Inventory cover targets (days)
- Price-change triggers (margin thresholds)
- Marketing / promo rules
- Loyalty on/off policy
- New decision rules based on observed failure modes in prior runs

Review the past rows of `results.tsv` — do **not** repeat an idea that already tested worse than baseline. Prefer small, isolated changes (one lever per experiment).

State the hypothesis in one sentence before editing.

### 2. Edit `.claude/agents/restaurant-manager.md`.
Use the `Edit` tool. **This is the only file you may change.** Do not touch any Python, data, or harness files.

### 3. Commit:
```bash
git add .claude/agents/restaurant-manager.md
git commit -m "exp-N: <one-line hypothesis>"
```

### 4. Run the experiment on BOTH seeds.

For each seed `(label, seed, start_date)` in `[("A", 20260424, "2026-04-24"), ("B", 20260801, "2026-08-01")]`:

```bash
python -m rest_sim init --days 30 --seed <SEED> --start-date <START> --cash 20000 --no-dashboard-browser > run.log 2>&1
```

Then dispatch the **restaurant-manager** subagent (via the Agent tool) with the exact brief from `/play-month` step 2. The subagent's own output does **not** land in your context — it returns a single summary message. Do not `tee`, do not read `run.log` unless a crash needs debugging.

After the subagent returns, capture the authoritative scorecard:

```bash
python -m rest_sim scorecard > scorecard_<LABEL>.json 2>&1
```

### 5. Extract the metric.
```bash
python3 -c "
import json
A = json.load(open('scorecard_A.json'))
B = json.load(open('scorecard_B.json'))
print(f\"score_A={A['score_eur']:.2f}\tscore_B={B['score_eur']:.2f}\tprofit_A={A['total_net_profit_eur']:.2f}\tprofit_B={B['total_net_profit_eur']:.2f}\tsat_A={A['mean_satisfaction']:.3f}\tsat_B={B['mean_satisfaction']:.3f}\trep_A={A['mean_reputation']:.3f}\trep_B={B['mean_reputation']:.3f}\twalk_A={A['total_walkouts']}\twalk_B={B['total_walkouts']}\")
"
```

If either JSON is missing/malformed or `bankrupt: true` was reached → status = `crash` (or `bankrupt`), **do not** update the ratchet; `tail -n 50 run.log`, attempt to understand, continue to step 7.

### 6. Decide: keep or revert.
- **Best-so-far** = the highest `score_A` in `results.tsv` to date with a non-bankrupt run on B (treat missing baseline as `-inf`, missing best_B as `-inf`).
- Keep iff `score_A > best_score_A` **AND** `score_B ≥ best_score_B − 0.05 × |best_score_B|` **AND** neither run is `bankrupt`.
- Otherwise `git reset --hard HEAD~1`. Status = `reject` / `regress_B` / `bankrupt` / `crash`.

Tiebreaker on equal `score_A`: simpler `restaurant-manager.md` (fewer lines) wins.

### 7. Append a row to `results.tsv`.
One tab-separated line:

```
<iter>\t<short_commit_sha_or_"reverted">\t<score_A>\t<score_B>\t<profit_A>\t<profit_B>\t<sat_A>\t<sat_B>\t<rep_A>\t<rep_B>\t<walk_A>\t<walk_B>\t<status>\t<one-line description>
```

Use `git rev-parse --short HEAD` **before** any reset to capture the experiment's SHA (even if reverted, so you can grep history).

### 8. Echo one line to the user.
Format: `iter N [status]: score_A=X (B=Y) profit_A=Z sat_A=S rep_A=R — <description>`. Nothing else. No per-day narration, no JSON dumps.

### 9. Loop.
Increment `N`. Return to step 1. **NEVER STOP** until the iteration budget is exhausted or the user interrupts. Do not ask the user whether to continue. Do not summarize partial progress.

---

## Hard rules

- **Only** `.claude/agents/restaurant-manager.md` may be edited. Never modify `rest_sim/`, `data/`, `game/`, or any Python file.
- **Never** commit `results.tsv`, `run.log`, or `scorecard*.json`.
- **Never** change either seed (`20260424`, `20260801`) — comparability is sacred.
- **Never** `tee` or print run.log to your own context. Redirect, grep, move on.
- **Never** re-init inside the subagent; the wrapper already did it.
- If you find yourself tempted to edit the harness "just to fix a bug" — stop. That invalidates every prior row.
- One lever per experiment. Multi-change diffs make the ratchet noisy.
- A mutation that wins on `A` but is bankrupt or regresses on `B` is **rejected** — no exceptions. The held-out check is the whole point of the v1 metric.

## When the budget is done

Print a short summary: top-3 rows from `results.tsv` by `score_A` (filtered to `kept` status), the final surviving `restaurant-manager.md`'s distinguishing features, and the open hypotheses worth trying next time. Then stop.
