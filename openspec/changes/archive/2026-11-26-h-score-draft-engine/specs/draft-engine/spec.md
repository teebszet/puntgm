## ADDED Requirements

### Requirement: Variance-aware category standardisation

The system SHALL standardise each player's per-category contribution into a comparable unit whose
denominator combines player-to-player spread with period-to-period (weekly) spread, rather than
player-to-player spread alone. Percentage categories SHALL be standardised in volume-weighted impact
form so that a high-percentage, low-volume player is not overvalued.

#### Scenario: Period variance affects standardised value

- **WHEN** two players have identical per-game category means but different week-to-week variance
- **THEN** their standardised values differ
- **AND** the player whose weekly production is more consistent is valued higher in that category

#### Scenario: Percentage categories are volume-weighted

- **WHEN** a player posts a high field-goal percentage on very few attempts
- **THEN** the standardised contribution reflects the low attempt volume
- **AND** it does not exceed that of a comparable-percentage, high-volume player

#### Scenario: Static reduction is available

- **WHEN** no roster context is supplied
- **THEN** the system produces a static ranked valuation from the same standardisation basis
- **AND** that valuation is usable as a complete draft ranking on its own

### Requirement: Pick value is category win probability

The system SHALL value a candidate pick by its effect on the probability of winning scoring
categories against an opponent, under a selectable objective: maximising the sum of per-category win
probabilities, or maximising the probability of winning a majority of categories including tie
handling.

#### Scenario: Objective is selectable by league setting

- **WHEN** the league is configured for an each-category objective
- **THEN** candidate values maximise the summed per-category win probability
- **AND** when configured for a most-categories objective, they maximise the probability of winning a majority

#### Scenario: A marginal category contribution is discounted

- **WHEN** a candidate would add production in a category the deciding team already wins with high probability
- **THEN** that contribution adds less pick value than an equal contribution in a category near even odds

### Requirement: Valuation is conditional on the roster already drafted

At each pick the system SHALL re-evaluate every available candidate against the deciding team's
already-drafted players, the opponents' known drafted players, and the expected contribution of the
picks both sides have yet to make.

#### Scenario: The same player is valued differently by different rosters

- **WHEN** the identical candidate is evaluated for two teams with different drafted rosters
- **THEN** the candidate receives different pick values for the two teams

#### Scenario: Unknown future picks contribute uncertainty

- **WHEN** a pick is evaluated early in the draft with many rounds remaining
- **THEN** the category differential carries variance from both sides' undrafted future picks
- **AND** that variance decreases as the draft progresses and fewer picks remain unknown

### Requirement: Category concentration emerges from optimisation

The system SHALL NOT require the manager to declare punted categories. Concentration on a subset of
categories SHALL arise as an outcome of maximising the objective.

#### Scenario: Concentration arises without being declared

- **WHEN** a roster has accumulated players weak in a category and the objective is maximised by conceding it
- **THEN** subsequent candidate values reflect that concession
- **AND** no category was marked as punted by the manager

#### Scenario: A concession is reversible

- **WHEN** the available player pool later makes a previously conceded category winnable again
- **THEN** candidate values in that category recover
- **AND** the engine is not locked into the earlier concession

### Requirement: Positional eligibility resolved by exact assignment

The system SHALL assign players to roster slots by solving an assignment problem over eligibility,
rather than by greedy slot-filling, and SHALL account for multi-position eligibility when valuing a
candidate.

#### Scenario: Multi-eligible players are priced for their flexibility

- **WHEN** two candidates have equal category contributions but one is eligible at more roster slots
- **THEN** the more flexible candidate receives the higher pick value

#### Scenario: An unfillable roster structure is reported

- **WHEN** the drafted roster cannot legally fill all required slots
- **THEN** the system reports the infeasibility rather than silently producing a valuation

### Requirement: Opponent drafting is modeled from ADP

The system SHALL model other drafters as selecting from an average-draft-position distribution with
noise, and SHALL use that model to estimate which players are likely to survive to the deciding
team's next pick.

#### Scenario: Survival odds inform waiting

- **WHEN** a candidate is very likely to be available at the deciding team's next pick
- **THEN** the system surfaces that survival probability alongside the candidate's pick value

#### Scenario: Scarcity reflects the remaining pool

- **WHEN** the remaining pool is depleted of contributors in a category
- **THEN** the modeled scarcity of that category increases
- **AND** candidates supplying it are valued accordingly

### Requirement: Measured per-player variance replaces uniform variance

The system SHALL derive each player's period-to-period variance from that player's measured game-log
production, and SHALL NOT assume a single shared variance across all players. A uniform-variance mode
SHALL be retained as a labeled baseline for comparison.

#### Scenario: Two players with equal means and different measured variance are valued differently

- **WHEN** two players have equal projected category means but different measured game-to-game variance
- **THEN** their pick values differ

#### Scenario: Uniform-variance baseline remains runnable

- **WHEN** the engine is run in uniform-variance mode
- **THEN** it produces valuations using a single shared variance
- **AND** the result is labeled as the baseline rather than the default

### Requirement: Draft replay validation against strategy baselines

The system SHALL replay drafts over a completed season using that season's realized production, and
SHALL grade competing draft strategies against each other by realized category outcomes. The
strategies compared SHALL include the dynamic engine, the static variance-aware reduction, the
existing z-score valuation, and an ADP-ordered baseline.

#### Scenario: Strategies are graded head-to-head

- **WHEN** a draft replay is run over a completed season
- **THEN** each strategy's realized category win rate is reported
- **AND** the comparison is head-to-head between strategies rather than against a single fixed field

#### Scenario: Replay requires no forward projections

- **WHEN** a draft replay is run for a completed season
- **THEN** it uses that season's realized production as the projection input
- **AND** it runs without any forward-season projection source configured

#### Scenario: Draft position is controlled for

- **WHEN** strategies are compared
- **THEN** results are reported across draft slots
- **AND** a strategy's advantage is not attributed to a favourable draft position

### Requirement: League format is configuration, not code

The engine SHALL take the category set, roster slot structure, team count, round count, and objective
function as configuration.

#### Scenario: Changing the category set requires no code change

- **WHEN** the engine is configured with a different category set
- **THEN** valuation and objective computation operate over the configured categories
