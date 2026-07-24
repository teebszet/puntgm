## REMOVED Requirements

### Requirement: Ranked streaming/waiver recommendations

**Reason:** The ranked single-list recommender is superseded by `matchup-projection` +
`call-feed`. Ranking the wire into one list discards the manager's judgment about team-build
strategy and risk/reward; the product instead surfaces relevance-weighted, scoreable signals.
The implemented baseline engine is retained in code only as a replay benchmark.

### Requirement: Perspective and matchup context

**Reason:** Perspective and matchup context are retained but move to `call-feed` (relevance)
and `matchup-projection` (category outcomes), where they drive a feed rather than a ranking.

### Requirement: Scoring window honors league cadence

**Reason:** Cadence-derived windows move to `matchup-projection` (the remaining-schedule sum)
and `call-feed`; they are no longer a property of a ranked recommender.

### Requirement: Point-in-time inputs only

**Reason:** The point-in-time guarantee is restated for the successor capabilities
(`matchup-projection` "Projection excludes future knowledge", `call-feed` "Point-in-time feed").

### Requirement: Explainable scores

**Reason:** Explainability moves from per-candidate score reasoning to signal evidence and
line-of-play rationale in `call-feed`.
