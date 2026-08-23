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

### Requirement: Static draft board with declared punt builds

The system SHALL produce a complete ranked draft board from the variance-aware basis, for the
balanced build and for declared punt builds, without requiring a live draft, league
authentication, or the dynamic optimizer. Each board SHALL report per-category contribution and
its rank difference against the z-score valuation over the same pool and category set.

#### Scenario: A board is produced for a punt build

- **WHEN** a board is requested with one or more categories declared as punted
- **THEN** those categories are excluded from the scored set
- **AND** the board is re-ranked over the remaining categories
- **AND** each player's per-category contribution covers only the scored categories

#### Scenario: The z-score comparison is like-for-like

- **WHEN** a board reports a player's rank difference against z-score
- **THEN** the z-score ranking is computed over the same player pool and the same category set

### Requirement: Published boards state their availability treatment

Because a player's availability materially changes their rank, a board SHALL state how
availability entered the ranking, and SHALL NOT present a ranking derived from a completed
season's realized availability as a forward-looking board without saying so.

#### Scenario: Provenance travels with the board

- **WHEN** a board is exported or rendered in any format
- **THEN** it carries a provenance statement naming the season measured, the availability
  treatment used, and — where availability is projected — the date the projection was made from

#### Scenario: Projected availability cannot see the season it ranks

- **WHEN** a board uses projected availability
- **THEN** a projection date is required
- **AND** the projection is fitted only from games known on or before that date

#### Scenario: A player with no prior history is not assumed durable

- **WHEN** a player in the pool has no games recorded before the projection date
- **THEN** their availability is taken from the fitted pool rate rather than assumed complete
