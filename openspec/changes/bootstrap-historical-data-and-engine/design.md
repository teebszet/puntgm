## Context

Greenfield project. The build order is deliberately inverted from a normal product: because distribution depends on a published track record, the decision engine and the data it reasons over must exist and be scoreable *before* any UI or Yahoo league sync. The single hardest correctness property in this milestone is **no lookahead bias** — a replay that accidentally sees the future will flatter the engine and the published track record will not survive contact with the live season. Every design decision below serves that property.

## Goals / Non-Goals

**Goals:**
- Backfill the full 2025-26 NBA season into a local, queryable store.
- Guarantee point-in-time correctness: any query can be constrained to "known as of date D."
- Provide a minimal, explainable decision engine for streaming/waiver ranking over a scoring window.
- Log every recommendation with enough context to (a) reproduce it and (b) later score it.
- Keep everything runnable locally by one person with no paid services.

**Non-Goals:**
- No replay harness / eval scoring yet (next change — this milestone only produces the data + engine + log it needs).
- No Yahoo OAuth or live league sync.
- No LLM calls yet — the skeleton engine uses deterministic heuristics so the harness has a baseline to beat and so behavior is testable.
- No UI, no MCP server, no daily-brief job.
- No projections model — recent per-game production stands in for now.

## Decisions

**D1. Data source: `nba_api` for backfill.** Free, covers box scores, schedules, and player game logs from NBA.com. Alternative: balldontlie paid tier — deferred; not worth the cost for a one-time historical backfill. Risk that NBA.com rate-limits/blocks cloud IPs is acceptable because backfill runs locally and once.

**D2. Storage: SQLite (single file) for v1.** Zero-setup, portable, transactional, trivial to snapshot into git-ignored data dir. Alternative: parquet + DuckDB — better for analytics scale but premature; revisit if query performance bites during replay.

**D3. Point-in-time model via effective-dating.** Every mutable fact (especially injury/availability, and any restated stat) is stored with a `known_from` date rather than overwritten. Reads take an `as_of` date and filter `known_from <= as_of`. Immutable facts (a completed game's box score) are dated by game date. This is the core anti-lookahead mechanism. Alternative: snapshot the whole DB per day — simpler conceptually but storage-heavy and awkward to query; effective-dating is the standard pattern.

**D4. Injury/availability is the lookahead danger zone.** Box scores are naturally point-in-time (a game that hasn't happened has no stats). Injuries are not: it is tempting to backfill "player X missed Jan 10–24" as a block. Instead capture dated injury-report states so the engine only sees the designation as it was reported that morning. Where perfectly-dated historical injury data is unavailable, record the limitation explicitly in the data provenance rather than silently importing hindsight.

**D5. Engine is deterministic and explainable.** Candidate score = f(games in window, recent per-game production, availability). Every score carries a human-readable reasoning string. This makes the engine (a) unit-testable, (b) a legitimate baseline for the future LLM engine to beat, and (c) already content-ready ("here's why it picked X").

**D6. Recommendation log is append-only and structured.** One row per recommendation: `created_at`, `as_of_date`, `league_state_ref`, `candidate`, `rank`, `score`, `reasoning`, `confidence`. Append-only so the track record can never be quietly rewritten — credibility of the eventual public claims depends on this.

## Risks / Trade-offs

- Lookahead bias creeping in via injury data → Mitigation: effective-dating (D3/D4), plus a test that asserts an `as_of` query never returns rows with `known_from > as_of`.
- NBA.com endpoint instability / rate limits → Mitigation: backfill locally, cache raw responses to disk, make the pipeline resumable/idempotent.
- Over-engineering the engine before the harness exists → Mitigation: keep it deterministic and small; the next change (harness) will reveal what actually matters.
- SQLite outgrown by replay scale → Mitigation: DuckDB is a drop-in later; keep data access behind a thin repository layer.

## Migration Plan

Greenfield — no migration. Deployment is "clone + install + run backfill script." Rollback is deleting the local data dir.

## Open Questions

- Best available source for *dated* historical injury reports (vs. retrospective injury blocks)? May require scraping archived reports; scope in the next change if it blocks scoring quality.
- Exact scoring-window definition (H2H weekly vs. rolling 7 days) — default to weekly (Mon–Sun) but keep it a parameter.
