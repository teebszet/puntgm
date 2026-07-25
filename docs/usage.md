# Usage

How to run and build on the weeks 1–2 foundation: the point-in-time data pipeline,
the simulate-first league state, the perspective-scoped skeleton engine, and the
append-only recommendation log.

> **Status:** skeleton. The engine is a deterministic baseline meant for the replay
> harness to beat — it *accepts* matchup/category context but does not yet optimize on
> it. The recommendation-log shape here is provisional and is being redesigned around a
> real H2H player's waiver process.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'          # nba_api + pytest + ruff
```

## CLI

Three subcommands under `python -m fantasy_gm.cli` (also installed as `fantasy-gm`).

### 1. `backfill` — load a season into the local store

```bash
# Deterministic offline season (no network) — good for dev, tests, and the harness:
python -m fantasy_gm.cli backfill --season 2025-26 --synthetic

# Real NBA.com backfill via nba_api (runs locally, once; resumable via the disk cache):
python -m fantasy_gm.cli backfill --season 2025-26
```

`--season` accepts `2025-26` (primary) or the validation seasons `2024-25` / `2023-24`.

### 2. `simulate` — generate a league over a backfilled season

League state is **simulate-first** (D7): rosters are snake-drafted from an ADP proxy and
a round-robin weekly matchup schedule is laid down. Everything is reproducible from
`--seed`.

```bash
python -m fantasy_gm.cli simulate --season 2025-26 --seed 1 \
    --teams 8 --roster 10 --cadence weekly-lock
# -> created simulated league sim-2025-26-1-8x10 (8 teams, cadence=weekly-lock)
```

`--cadence` is `weekly-lock` or `daily-change` (D9) and drives the scoring window.

### 3. `recommend` — rank waiver candidates for one team

Perspective-scoped: you pass *whose* decision it is (`--league` + `--team`). Every call
is written to the append-only log.

```bash
python -m fantasy_gm.cli recommend --as-of 2025-11-14 \
    --league sim-2025-26-1-8x10 --team T00 --top 5
```

```
perspective: league=sim-2025-26-1-8x10 team=T00 period=3 opp=T03 as_of=2025-11-14
  #1  BBB Player 9   score=31.5111  conf=0.845 — 3 game(s) this week; recent production
      16.3/g; healthy; vs T03: targets contested cats reb, ast, blk
  ...
logged 5 recommendation(s); log now holds 5 row(s)
```

### 4. `project` — category outcome projections (the spine)

```bash
python -m fantasy_gm.cli project --as-of 2025-11-14 --league sim-2025-26-1-8x10 --team T00
```

Prints, per category, the projected end-of-period totals for both teams, the win
probability, and a **safe / contested / gone** label. Contested cats are the ones worth
fighting for. Variance-aware (a lead in blocks is less safe than the same lead in points)
and availability-reactive (an OUT player re-opens categories).

### 5. `feed` — live signals + end-of-day reconciliation

```bash
python -m fantasy_gm.cli feed --as-of 2025-11-14 --league sim-2025-26-1-8x10 --team T00 --all
```

Two parts, both written to the append-only call-feed log:

- **Live signals** — typed observations (usage trends, role changes, availability,
  opponent moves) graded on a soft→strong spectrum where
  `strength = confidence × impact-on-a-contested-cat × relevance-to-your-build`. Strong,
  relevant signals show by default; `--all` includes soft ones.
- **End-of-day reconciliation** — candidate add/drop moves, each labeled by the category it
  most improves ("contest REB") with the projected win-prob impact per category, and flagged
  if it drops a player who hasn't played yet.

## Library usage

Everything the CLI does is available programmatically.

```python
from fantasy_gm.data.store import Store
from fantasy_gm.data.synthetic import seed_synthetic_season
from fantasy_gm.data.simulate import simulate_league
from fantasy_gm.engine.engine import DecisionEngine
from fantasy_gm.log.reclog import RecommendationLog

store = Store(":memory:")                     # or Store("data/fantasy_gm.sqlite")
seed_synthetic_season(store, season="2025-26", seed=7)
league = simulate_league(store, season="2025-26", seed=1, cadence="weekly-lock")

recs = DecisionEngine().recommend(store, league, team_id="T00", as_of="2025-11-14", top_n=5)
RecommendationLog(store).append(recs)

for r in recs:
    print(r.rank, r.candidate_name, r.score, r.reasoning)
```

### Importing your own real league (validation set, D7)

`import_league_export` loads an **already-fetched, read-only** Yahoo export — no network,
no writes to your league. The live OAuth fetch is deferred to the league-sync change.

```python
from fantasy_gm.data.yahoo_import import import_league_export

league_id = import_league_export(store, {
    "league_id": "yahoo-431.l.12345", "name": "My Dynasty",
    "season": "2024-25", "cadence": "daily-change",
    "teams": [{"team_id": "1", "name": "..."}, ...],
    "roster_events": [...],   # omit -> provenance recorded for missing point-in-time history
    "matchups": [...],
})
```

## The point-in-time model (anti-lookahead)

The core correctness property is that a recommendation for date *D* is a pure function of
what a manager knew on the morning of *D*. Enforced in `fantasy_gm/data/store.py`:

- **Outcomes** (box scores/results), **availability** (injury designations), and **roster
  moves** are gated by `<= as_of`. As-of reads never return a record known only after *D*.
- **Schedule** (which teams play which dates) is *a priori* knowledge — published
  preseason — so upcoming-window schedule reads are intentionally **not** gated. Only
  outcomes/availability/rosters are.
- Availability carries `source` + `confidence`, so future media-sourced enrichment can be
  layered in without overwriting official designations.

The lookahead guard is asserted in `tests/test_asof_guard.py`.

## Lineup cadence & scoring window

`lineup_cadence` is a per-league setting because the user's own league moved
`weekly-lock` → `daily-change` ~2 seasons ago (D9). The scoring window derives from it:

- `weekly-lock` → the current Monday–Sunday period.
- `daily-change` → a single day, evaluated at daily granularity.

## Recommendation log schema

Append-only (`recommendation_log` table). One row per ranked candidate per call. The log
exposes only `append` + reads — no update/delete — so the published track record can't be
quietly rewritten.

| Column | Type | Meaning |
|---|---|---|
| `id` | int | Auto-increment PK; never reused |
| `created_at` | ISO ts | When the call was logged (UTC wall clock) |
| `as_of_date` | date | Decision date — the only knowledge the engine could see |
| `league_state_ref` | text | `league@as_of#team` — reloads the exact inputs |
| `league_id` | text | Perspective: which league |
| `team_id` | text | Perspective: whose decision |
| `period_index` | int | Scoring period (week) index |
| `opponent_team_id` | text | That week's H2H opponent |
| `candidate_id` | text | Recommended wire pickup (player id) |
| `candidate_name` | text | Player display name |
| `rank` | int | Position in the ranked list |
| `score` | real | Deterministic engine score |
| `reasoning` | text | Human-readable explanation of the signals used |
| `confidence` | real | Bounded [0,1] |

Reproducing a call: `as_of_date` + `league_id` + `team_id` are sufficient to re-run the
engine and obtain the same recommendation (`tests/test_reclog.py`).

## Testing & lint

```bash
pytest -q
ruff check fantasy_gm tests
```

## Call-feed log

The reframed log (`fantasy_gm/log/reclog.py`, `FeedLog`) is append-only with two record
types: **signal** (subject, owner class, type, evidence, strength, affected cats) and
**reconciliation-move** (add, drop, line of play, projected per-category impact). Replay
scoring lives in `fantasy_gm/engine/scoring.py`: `grade_move` grades a suggested move by its
*realized* category impact vs. standing pat (a counterfactual over the actual box scores),
and `calibration` checks whether categories called "safe" actually held. The old
`RecommendationLog` (ranked rows) is retained as the replay baseline.

## Validating assumptions

The projection has parameters that were *asserted* (from a domain expert) rather than
measured. Per the project principle, those are provisional until validated on real data
(`openspec/changes/call-feed-and-matchup-projection/assumptions.md` is the full ledger).

```bash
# Measure per-category variance (coefficient of variation) from a backfilled season.
python -m fantasy_gm.cli validate --season 2025-26
```

`fantasy_gm/validation/` provides `measure_category_cv` (the variance ranking), a
`derive_variance_profile` the projector can consume via `Projector(variance_profile=…)`
(falling back to the provisional grouping otherwise), and `bootstrap_category_winprob` — a
Monte-Carlo win-prob to check the projector's normal approximation, which is weakest for
low-count categories (blocks/steals). **These only mean something on real `nba_api` data**;
the synthetic season is generated from the same assumptions, so it validates the mechanism,
not the claim.

## Current limitations

- Projection uses a normal approximation and treats games as independent (both flagged in the
  assumptions ledger; `bootstrap_category_winprob` is the check). Percentage categories are now
  volume-weighted (fixed).
- Signal detection is a deterministic subset (usage trend, availability, opponent move);
  efficiency-regression and richer role signals are future work.
- Real NBA.com payload parsing in `nba_source.py` is stubbed for the networked machine;
  offline flows use the synthetic season.
