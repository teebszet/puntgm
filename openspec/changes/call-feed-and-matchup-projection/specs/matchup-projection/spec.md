## ADDED Requirements

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

The projection SHALL account for per-category variance profiles, treating points and rebounds
as lower-variance and steals, blocks, and assists as higher-variance, so that a numeric lead
in a high-variance category is not called safe as readily as the same lead in a low-variance one.

#### Scenario: High-variance lead is less safe than an equal low-variance lead

- **WHEN** the deciding team leads two categories by comparable projected margins, one low-variance and one high-variance
- **THEN** the high-variance category is assigned a lower win probability (closer to contested) than the low-variance category

### Requirement: Availability-reactive projection

The projection SHALL react to availability changes: when a player's designation changes as of a
date, that player's contribution to remaining games updates, and affected categories are re-projected.

#### Scenario: Injury re-opens a category

- **WHEN** a rostered player is designated OUT as of date D and re-projected
- **THEN** that player contributes no expected production for remaining games while OUT
- **AND** categories that depended on that player move toward contested or gone
