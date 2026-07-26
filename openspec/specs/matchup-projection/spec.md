# matchup-projection Specification

## Purpose
TBD - created by archiving change call-feed-and-matchup-projection. Update Purpose after archive.
## Requirements
### Requirement: Per-category outcome projection

The system SHALL project, for each scoring category in a matchup, the end-of-period outcome
for both teams as a distribution derived from the current point-in-time tally plus the sum
over each rostered player's remaining scheduled games of an expected-per-game value carrying
a confidence/variance band.

#### Scenario: Project a category to period end

- **WHEN** a matchup is projected as of date D
- **THEN** each category yields, for both teams, a projected total with an uncertainty band
- **AND** the projection uses only production known on or before D and the a-priori remaining schedule

#### Scenario: Projection excludes future knowledge

- **WHEN** a category is projected as of date D
- **THEN** expected-per-game estimates derive only from games completed on or before D
- **AND** no game result dated after D influences the projection

### Requirement: Win probability and safe/contested/gone read

For each category the system SHALL compare the two teams' projected distributions to produce
a win probability for the deciding team and a label of safe, contested, or gone.

#### Scenario: Label a category from its win probability

- **WHEN** a category's two projected distributions are compared
- **THEN** a win probability for the deciding team is produced
- **AND** the category is labeled safe, contested, or gone according to that probability

#### Scenario: Uncertainty is not overstated early in the period

- **WHEN** few games in the period have been played
- **THEN** more categories are labeled contested rather than safe or gone
- **AND** the labels sharpen as more of the period's games resolve

### Requirement: Variance-aware projection

The projection's per-category uncertainty SHALL be derived from the **measured** per-player
per-game standard deviation (Σ over remaining games of σ²), with **no hand-set category variance
multiplier**. A category's volatility therefore emerges from the data, so a numeric lead in a
genuinely more volatile category is not called safe as readily as the same lead in a stable one.
Validated on real 2025-26 data (`assumptions.md` A1/A2/A4): game-to-game production is
approximately independent (lag-1 autocorrelation near zero), so Σ rg·σ² is the correct spread and
a multiplier would double-count the σ already in the model.

#### Scenario: A higher-variance lead is less safe than an equal lower-variance lead

- **WHEN** the deciding team leads two categories by comparable projected margins, one whose players have a larger measured per-game σ than the other
- **THEN** the higher-σ category is assigned a lower win probability (closer to contested) than the lower-σ category

#### Scenario: No hard-coded category variance grouping is used

- **WHEN** the projection computes a category's uncertainty band
- **THEN** it uses only the measured per-player σ over remaining games
- **AND** applies no per-category multiplier or asserted variance grouping

### Requirement: Availability-reactive projection

The projection SHALL react to availability changes: when a player's designation changes as of a
date, that player's contribution to remaining games updates, and affected categories are re-projected.

#### Scenario: Injury re-opens a category

- **WHEN** a rostered player is designated OUT as of date D and re-projected
- **THEN** that player contributes no expected production for remaining games while OUT
- **AND** categories that depended on that player move toward contested or gone

