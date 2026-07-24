## ADDED Requirements

### Requirement: Ranked streaming/waiver recommendations

The decision engine SHALL, given a point-in-time league state, a perspective (which team in the league is deciding), and a date, return a ranked list of available candidates to add for the upcoming scoring window.

#### Scenario: Produce a ranked candidate list

- **WHEN** the engine is asked for recommendations given a league state, a perspective team, and date D
- **THEN** it returns available candidates ranked for the scoring window starting at D
- **AND** each candidate has a numeric score and a rank

#### Scenario: Only available players are recommended

- **WHEN** the engine builds recommendations for date D
- **THEN** every recommended candidate is on the waiver wire in the given league state
- **AND** no player rostered by any team in that league appears as a candidate

### Requirement: Perspective and matchup context

The engine SHALL accept the deciding team's weekly matchup opponent and the current per-category standing as part of its input, so that opponent-relative optimization can be added without changing callers. The skeleton engine MAY leave this context unweighted; it SHALL NOT reject or ignore its presence.

#### Scenario: Engine accepts matchup context

- **WHEN** the engine is called with a league state whose active matchup and per-category tally are populated
- **THEN** the call succeeds and the returned recommendations are scoped to the deciding team's perspective
- **AND** the reasoning may reference the matchup context where the engine uses it

### Requirement: Scoring window honors league cadence

The scoring window SHALL default to a weekly period (Monday–Sunday) and SHALL be derived from the league's `lineup_cadence`, so a daily-change league can be evaluated per day and a weekly-lock league per week.

#### Scenario: Window follows cadence

- **WHEN** the engine builds recommendations for a league with `lineup_cadence` = weekly-lock
- **THEN** the scoring window spans the current Monday–Sunday period
- **WHEN** the league's `lineup_cadence` = daily-change
- **THEN** the window is evaluated at daily granularity

### Requirement: Point-in-time inputs only

The engine SHALL derive recommendations exclusively from data known as of the requested date.

#### Scenario: No future data influences a recommendation

- **WHEN** the engine computes recommendations as of date D
- **THEN** it reads underlying data only through the as-of query for date D
- **AND** no game result or availability designation dated after D affects the output

### Requirement: Explainable scores

Each recommendation SHALL include a human-readable reasoning string describing why the candidate was ranked as it was.

#### Scenario: Reasoning accompanies every candidate

- **WHEN** the engine returns a ranked candidate
- **THEN** the candidate includes a reasoning string referencing the signals used (e.g., games in window, recent production, availability)
