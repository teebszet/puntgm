## ADDED Requirements

### Requirement: Ranked streaming/waiver recommendations

The decision engine SHALL, given a point-in-time league state and a date, return a ranked list of available candidates to add for the upcoming scoring window.

#### Scenario: Produce a ranked candidate list

- **WHEN** the engine is asked for recommendations given a league state and date D
- **THEN** it returns available candidates ranked for the scoring window starting at D
- **AND** each candidate has a numeric score and a rank

#### Scenario: Only available players are recommended

- **WHEN** the engine builds recommendations for date D
- **THEN** every recommended candidate is on the waiver wire in the given league state
- **AND** no rostered player appears as a candidate

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
