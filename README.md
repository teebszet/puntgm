# Fantasy NBA GM

An AI **Co-GM** for head-to-head category fantasy basketball. It reasons over live NBA data to make context-heavy calls — schedule-aware streaming, waiver pickups, punt-build strategy, playoff-week planning — for the kind of serious H2H grinder who already pays for tools like Basketball Monster or Hashtag.

## Why this exists / strategy

The wedge is an **in-season Co-GM for Yahoo H2H category leagues**, delivered as a hybrid:

- **Chat Co-GM** — a BYO-LLM MCP server the user connects to their own Claude/ChatGPT (near-zero inference cost to us).
- **Proactive daily brief** — a small hosted job on our tokens (bounded, ~$4–7/user/season).
- One shared **data layer** underneath both.

Distribution is the real bet, and its principle is **"the product's track record is the content."** A replay harness scores the decision engine against last season's data and publishes verifiable results *before* the live season. That inverts the build order: the **decision engine + its data + a recommendation log come first**; Yahoo league sync (OAuth) comes later.

See the full validation memo (kept outside this repo) for market, competitors, feasibility, and the full roadmap.

## Current milestone — Weeks 1–2

Stand up the foundation the replay harness will need:

1. **Historical data pipeline** — backfill the full 2025-26 NBA season, stored **point-in-time** (no lookahead).
2. **Point-in-time league state** — rosters, weekly matchups, and per-category tallies, because H2H waiver play is opponent- and category-relative. Sourced **simulate-first** (reproducible generated leagues), plus an optional read-only import of your own past leagues as a validation set.
3. **Skeleton decision engine** — deterministic, explainable, **perspective-scoped** (whose team + which weekly opponent) streaming/waiver ranking for a cadence-aware scoring window.
4. **Recommendation log** — append-only record of every call (as-of date, inputs, perspective, output, reasoning, confidence) — the shared source of truth for evals and content.

No UI, no live OAuth sync, no LLM calls yet. The skeleton engine *accepts* matchup/category context but does not yet optimize on it — full opponent-relative reasoning is the next engine's job.

Tracked as an OpenSpec change: `openspec/changes/bootstrap-historical-data-and-engine/`.

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
npx @fission-ai/openspec show bootstrap-historical-data-and-engine
npx @fission-ai/openspec validate bootstrap-historical-data-and-engine --strict
```

When the milestone is implemented and merged, archive it:

```bash
npx @fission-ai/openspec archive bootstrap-historical-data-and-engine
```

## Stack

Python 3.11+, `nba_api` (free NBA.com endpoints) for backfill, SQLite for point-in-time storage. `balldontlie` (paid, licensed-enough) is deferred to the live-data change.
