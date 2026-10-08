## ADDED Requirements

### Requirement: Per-game rates are measured over games actually played

The per-game category basis SHALL measure rates over the games a player actually played.
A game-log row with no recorded playing time (a DNP row) SHALL contribute to the availability
model's games-played history and SHALL NOT contribute to per-game rate means or to a player's
pool eligibility.

#### Scenario: A DNP-heavy player's rate is not diluted

- **WHEN** a player's 2025-26 season includes zero-stat DNP rows alongside games played
- **THEN** his per-game category rates are computed over the games played only
- **AND** the DNP rows still count as games not played for the availability projection

#### Scenario: The basis provenance line states the rule

- **WHEN** a board is exported or rendered
- **THEN** its basis line names that rates are over games played and DNPs are carried by
  the availability term

### Requirement: The draft pool reflects the current role, not a career average

The draft pool SHALL be ranked by the player's most recent measured role (2025-26 minutes per
game), not by an average that spans earlier seasons. Players without NBA history SHALL be
ranked by their derived depth on the projected roster.

#### Scenario: A veteran whose minutes fell is not pool-ranked on his career average

- **WHEN** the pool is built for the 2026-27 draft
- **THEN** each player's rank input is his 2025-26 minutes per game
- **AND** no earlier season's minutes enter the ranking

#### Scenario: Incoming players are placed by derived depth

- **WHEN** a player has no NBA game logs
- **THEN** his pool position derives from his ranked depth on his projected roster

### Requirement: News overrides place and price a player the model cannot

The system SHALL accept effective-dated manual overrides per player — expected games played,
expected role, and a note — which force the player into the draft pool and replace the
model's projection for him. Every override applied SHALL be visible in the board's provenance
and in any recommendation that includes the player.

#### Scenario: A player who missed the prior season is draftable

- **WHEN** an override names a player with no 2025-26 logs (e.g. Haliburton, Lillard, Irving)
- **THEN** that player enters the draft pool
- **AND** his projected production follows the override, labeled as override-derived

#### Scenario: An override without projection data is not silently dropped

- **WHEN** an override row is loaded for a player the basis cannot otherwise project
- **THEN** the board reports that the override was applied
- **AND** a malformed or stale override is surfaced, never silently ignored

#### Scenario: The manager sees which rows are overrides

- **WHEN** a recommendation or board row names an overridden player
- **THEN** the row is marked as override-priced with its note

### Requirement: A forward basis is available and clearly labeled

The board SHALL offer a `projected` basis built from the derived minutes/role model — which
projects usage onto the player's 2026-27 roster — alongside the default `measured` basis.
The projected basis SHALL be labeled in every rendering with its projection date and the
standing caveat that its minutes edge over naive carry-forward is unproven (2.11
inconclusive).

#### Scenario: The manager can flip the basis

- **WHEN** the board or live session is built with `--basis projected`
- **THEN** rates come from the derived model reacting to team changes
- **AND** the basis line carries the projection date and the unproven-edge caveat

#### Scenario: The default basis remains measured

- **WHEN** no basis is specified
- **THEN** the board builds on measured 2025-26 production as it does today

### Requirement: Recommendations carry market context and configurable columns

Each recommendation candidate SHALL be able to display the player's Yahoo ADP, the ADP
deviation from the board rank, and the categories the candidate most *negatively* contributes
to — in addition to the existing rank, value, value-over-safe, survival, and top-category
columns. The columns shown SHALL be configurable by the manager, and the configuration
SHALL persist.

#### Scenario: The card shows the market next to the model

- **WHEN** a recommendation is rendered with default columns
- **THEN** each row shows board rank, value, value-over-safe, survival, Yahoo ADP, top
  categories, and top negative categories

#### Scenario: A player without ADP shows its absence explicitly

- **WHEN** a candidate has no ADP row
- **THEN** its ADP cell renders as an explicit absence, never a zero or a blank guess

#### Scenario: The manager chooses the columns

- **WHEN** the manager toggles columns in the terminal (`--columns`) or on the page
  (column picker)
- **THEN** the card renders the chosen set
- **AND** the page choice persists across sessions

### Requirement: The recommendation view shows the roster's category standing against the field

After every pick, the system SHALL present, per category, the deciding team's projected
profile against both the field's average roster and the field's strongest roster — as
display, computed from current draft state. This view SHALL NOT feed a re-ranking of
candidates: the optimizer measured behind the static board in replay (48/48) stays off, and
the existing engine hook remains for when one is cleared.

#### Scenario: The differential updates as picks land

- **WHEN** any pick is recorded
- **THEN** the per-category differential (our roster vs field average vs field strongest)
  is recomputed and shown

#### Scenario: The differential is display-only

- **WHEN** the differential is computed
- **THEN** candidate ordering still comes from the static board (or an explicitly supplied
  engine), never from the differential view

### Requirement: Punt scenarios are explorable on the clock

Given the current draft state, the system SHALL re-rank the remaining pool under each punt
build — the named builds and a manager-specified category set — and show, per build, the top
candidates and an outlook of what the manager's roster projects to win under that build.
Computation SHALL respect the on-the-clock budget: beyond it, the panel degrades to the last
complete computation, marked as such.

#### Scenario: Punt builds rank against the current state

- **WHEN** the manager opens the punt panel mid-draft
- **THEN** each build's candidate ranking excludes drafted players and is conditioned on the
  roster already drafted

#### Scenario: A custom punt set is accepted

- **WHEN** the manager names categories to drop (e.g. ft%,pts)
- **THEN** the board re-ranks over the remaining categories for that build

#### Scenario: The panel never costs the clock

- **WHEN** computing all builds would exceed the budget
- **THEN** the panel shows the last complete build set with its as-of pick number, marked
  as stale

### Requirement: The watch page is a drafting dashboard

The watch page SHALL present a dense drafting dashboard: a fixed header with the pick clock,
the recommendation card with its column picker, the category differential, the punt panel, a
roster rail for the deciding team, and a compact pick log. Layout SHALL be usable on a phone
in portrait. The page SHALL keep its static-page + `/state.json` contract; new state fields
SHALL be additive so a stale page still renders a new state.

#### Scenario: The dashboard fits a drafting screen

- **WHEN** the page renders during an active draft
- **THEN** clock, recommendation card, differential, punt panel, roster rail, and pick log
  are each visible or one tap away, without blog-style empty space

#### Scenario: The state contract stays additive

- **WHEN** the watcher writes new fields (ADP, negative categories, differential, builds)
- **THEN** an older cached page still renders the fields it knows and ignores the rest
