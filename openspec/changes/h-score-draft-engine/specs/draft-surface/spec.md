## ADDED Requirements

### Requirement: Live draft state ingestion with a manual fallback

The system SHALL maintain the state of an in-progress draft — which players are gone, to which teams,
and whose pick is next — from picks ingested either automatically from the league platform or entered
manually. Manual entry SHALL be sufficient on its own to operate the tool through a complete draft.

#### Scenario: Automatic ingestion updates draft state

- **WHEN** picks are ingested from the league platform during a draft
- **THEN** those players are marked unavailable and assigned to the drafting teams
- **AND** recommendations reflect the updated pool

#### Scenario: Manual entry alone can drive a full draft

- **WHEN** no automatic ingestion is configured
- **THEN** picks can be entered manually
- **AND** the tool operates through a complete draft on manual entry alone

#### Scenario: Ingestion failure degrades rather than blocks

- **WHEN** automatic ingestion becomes unavailable mid-draft
- **THEN** the system reports the failure
- **AND** manual entry continues to operate against the state ingested so far

#### Scenario: Ingested state is reconciled against the platform

- **WHEN** ingested draft state disagrees with the platform's record of picks made
- **THEN** the discrepancy is surfaced rather than silently reconciled

### Requirement: On-the-clock recommendation

When the deciding team is on the clock, the system SHALL present ranked candidates with each
candidate's pick value, the categories it most affects, and its probability of surviving to the
deciding team's next pick.

#### Scenario: Recommendations are presented on the clock

- **WHEN** the deciding team is on the clock
- **THEN** ranked candidates are presented with pick value, category impact, and survival probability

#### Scenario: The manager retains the decision

- **WHEN** the manager drafts a player other than the top recommendation
- **THEN** the pick is accepted
- **AND** subsequent recommendations are conditioned on the roster actually drafted

### Requirement: Recommendations are produced within draft-clock time

The system SHALL produce on-the-clock recommendations fast enough to be usable under a live draft
clock, and SHALL degrade to a simpler valuation rather than exceed the available time.

#### Scenario: A slow evaluation degrades rather than blocks

- **WHEN** full evaluation would exceed the time available on the clock
- **THEN** the system returns a result from a reduced evaluation
- **AND** it indicates that the reduced evaluation was used

### Requirement: Completed draft grading

The system SHALL grade a completed draft board, reporting for each team a projected category profile
and standing, the build the roster represents, and the picks that most changed that team's projected
outcome.

#### Scenario: A completed board is graded

- **WHEN** a completed draft board is supplied
- **THEN** each team receives a projected category profile and standing
- **AND** the categories the roster is built to win and to concede are identified

#### Scenario: Costly picks are identified

- **WHEN** a team's draft is graded
- **THEN** the picks that most reduced that team's projected outcome are identified
- **AND** each is reported against the alternative available at that pick

#### Scenario: Grading works for a draft the engine did not make

- **WHEN** the supplied board contains no picks made by the engine
- **THEN** grading still produces a projected profile, standing, and costly-pick analysis
