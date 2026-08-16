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

### Requirement: Expected games played, not scheduled games

The projection SHALL scale each player's remaining **scheduled team games** by a measured
participation rate — the fraction of their team's recent scheduled games in which the player
actually appeared — rather than assuming every scheduled game is played. Measured on the real
2025-26 season, mean participation is **0.49** (rosterable pool 0.73, off-pool wire 0.45), so
the assumption overstated every projection and overstated wire candidates roughly twice as much
as rostered ones.

The rate SHALL be the raw trailing rate over a short window, not shrunk toward a prior:
participation is strongly bimodal (66.6% of observations are 0-of-5 or 5-of-5), so shrinking
toward the mean lands where almost nobody sits. On held-out data the raw rate beats Beta
shrinkage and an empirical calibration map (MAE 0.177 / 0.207 / 0.207 against 0.509 for
assume-all-played). The participation window is deliberately shorter than the production window,
because availability is a current-state fact while production is a stable skill.

Per-category uncertainty SHALL include the resulting did-not-play risk: production per scheduled
game is a mixture (zero with probability 1−q, otherwise the measured distribution), giving a
variance per scheduled game of `q·σ² + q(1−q)·μ²`. The second term is what makes a perfectly
consistent scorer who plays half the time an uncertain contributor rather than a certain one.

#### Scenario: A part-time player projects less than a full-time one

- **WHEN** two players have identical measured per-game production, but one appeared in every recent scheduled team game and the other in half of them
- **THEN** the half-time player's projected contribution over the remaining schedule is proportionally lower
- **AND** the ranking of waiver candidates reflects production per *scheduled* game, not per *played* game

#### Scenario: Did-not-play risk is uncertainty, not just a smaller mean

- **WHEN** a player with zero measured game-to-game variance has a participation rate below 1
- **THEN** the projection assigns that player's contribution a non-zero standard deviation

### Requirement: Availability-reactive projection

The projection SHALL react to availability changes: when a player's designation changes as of a
date, that player's contribution to remaining games updates, and affected categories are re-projected.

#### Scenario: Injury re-opens a category

- **WHEN** a rostered player is designated OUT as of date D and re-projected
- **THEN** that player contributes no expected production for remaining games while OUT
- **AND** categories that depended on that player move toward contested or gone

