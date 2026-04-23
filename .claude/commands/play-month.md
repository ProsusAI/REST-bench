---
description: Reset the restaurant sim and let the restaurant-manager agent play a full month.
argument-hint: "[days] [seed] [cash]"
allowed-tools: Bash, Agent
---

Play the restaurant management simulation end-to-end.

**Arguments** (all optional, positional):
- `$1` = number of days (default 30)
- `$2` = random seed (default 20260423)
- `$3` = starting cash in EUR (default 20000)

## Steps

1. Reset the game state by running:

   ```bash
   python rest_cli.py init --days ${1:-30} --seed ${2:-20260423} --cash ${3:-20000}
   ```

   Print the init response so the user sees the starting conditions.

2. Launch the **restaurant-manager** subagent with this exact brief:

   > The game has just been initialized (do NOT run `init` again). Your working directory is the project root. Play the full simulation end-to-end: each day, read `python rest_cli.py status`, decide on actions using the decision-lever CLI commands, then run `python rest_cli.py advance`. Stop when `python rest_cli.py done` returns `{"done": true}`, then call `python rest_cli.py scorecard` and write a concise post-mortem covering: final cash delta vs starting cash, mean satisfaction/reputation, the three decisions that worked best, one mistake, and what you'd change next time. Keep per-day commentary to at most two short sentences.

3. When the subagent returns, relay its post-mortem to the user verbatim plus a one-line headline (e.g. "Final cash delta: +€X ; reputation 4.2 → 4.3"). Do not re-summarize the per-day decisions.
