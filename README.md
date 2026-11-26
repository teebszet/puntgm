# Fantasy NBA GM

An AI **Co-GM** for head-to-head category fantasy basketball. It reasons over live NBA data to make context-heavy calls — schedule-aware streaming, waiver pickups, punt-build strategy, playoff-week planning — for the kind of serious H2H grinder who already pays for tools like Basketball Monster or Hashtag.

## Why this exists / strategy

The wedge is an **in-season Co-GM for Yahoo H2H category leagues**, delivered as a hybrid:

- **Chat Co-GM** — a BYO-LLM MCP server the user connects to their own Claude/ChatGPT (near-zero inference cost to us).
- **Proactive daily brief** — a small hosted job on our tokens (bounded, ~$4–7/user/season).
- One shared **data layer** underneath both.

Distribution is the real bet, and its principle is **"the product's track record is the content."** A replay harness scores the decision engine against last season's data and publishes verifiable results *before* the live season. That inverts the build order: the **decision engine + its data + a recommendation log come first**; Yahoo league sync (OAuth) comes later.

See the full validation memo (kept outside this repo) for market, competitors, feasibility, and the full roadmap.

## Current state

Live on Tim's Yahoo H2H 9-cat league (12-team, 2026-27 season in progress):

- **Data layer** — point-in-time store fed by a daily wire refresh (statuses, news,
  roster moves, dated lineup snapshots, ESPN schedule, box-score backfill). Real
  banked totals; the store is the boards' basis of record, Yahoo's scoreboard the
  ground-truth check.
- **Decision engine** — all-9-cat win projections with per-category σ and 25–75%
  bands, differential views, punt panels, news-driven re-pricing, and a
  schedule-aware games-remaining counter verified against Yahoo.
- **Web tools** — puntgm.com/tools: fixture grid, wire-lines waiver board, and
  matchup table behind Yahoo OAuth. Manual: `docs/webtools.md`. The daily digest
  posts are retired (2026-11-24); this web surface replaced them.
- **Draft stack** — the h-score draft engine that ran the 2026-26 draft night
  (G-score board, survival-aware reach), validated in the replay harness.

Distribution principle unchanged: **"the product's track record is the content."**
Replay-harness results live with the archived OpenSpec changes under
`openspec/changes/archive/`.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'          # nba_api + pytest + ruff

# Backfill a season. --synthetic generates a deterministic offline season
# (no network); drop it to pull the real season via nba_api.
python -m fantasy_gm.cli backfill --season 2025-26 --synthetic

# Generate a simulated league over that season (simulate-first, D7).
python -m fantasy_gm.cli simulate --season 2025-26 --seed 1 --cadence weekly-lock

# Rank waiver candidates from one team's perspective for its upcoming window.
# Every call is written to the append-only recommendation log.
python -m fantasy_gm.cli recommend --as-of 2025-11-11 --league sim-2025-26-1-8x10 --team T00

pytest -q && ruff check fantasy_gm tests
```

The local store (`data/`) and raw caches are git-ignored. The anti-lookahead contract
lives in `fantasy_gm/data/store.py`: outcomes/availability/roster-moves are gated by an
as-of date, while the schedule is treated as a priori knowledge.

## Spec-driven development (OpenSpec)

This repo uses [OpenSpec](https://github.com/Fission-AI/OpenSpec). Proposals live in `openspec/changes/` and current capabilities in `openspec/specs/`.

```bash
npx @fission-ai/openspec list                 # active changes
npx @fission-ai/openspec validate <change-name> --strict
```

When the milestone is implemented and merged, archive it:

```bash
npx @fission-ai/openspec archive <change-name>
```

## Stack

Python 3.11+, `nba_api` (free NBA.com endpoints) for backfill, SQLite for point-in-time storage. `balldontlie` (paid, licensed-enough) is still deferred; live league sync runs through Yahoo OAuth.
