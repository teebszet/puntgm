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
2. **Skeleton decision engine** — deterministic, explainable streaming/waiver ranking for a scoring window.
3. **Recommendation log** — append-only record of every call (as-of date, inputs, output, reasoning, confidence) — the shared source of truth for evals and content.

No UI, no OAuth, no LLM calls yet.

Tracked as an OpenSpec change: `openspec/changes/bootstrap-historical-data-and-engine/`.

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
