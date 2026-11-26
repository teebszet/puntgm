## ADDED Requirements

### Requirement: Draft-pick record type

The log SHALL record a draft-pick record for each pick the engine recommends, capturing the draft
state the recommendation was made against, the candidates considered with their pick values, the
recommendation, and the pick actually made. Append-only integrity SHALL hold as for existing record
types.

#### Scenario: A recommended pick is logged

- **WHEN** the engine recommends a pick
- **THEN** a draft-pick record is appended capturing the draft state, the ranked candidates with
  their values, the recommendation, and the pick actually made

#### Scenario: A pick against the recommendation is logged as such

- **WHEN** the manager drafts a player other than the recommended one
- **THEN** the record captures both the recommendation and the divergent pick

#### Scenario: Draft-pick records are reproducible

- **WHEN** a draft-pick record is re-run from its recorded state, projection source, and settings
- **THEN** the same candidate values and recommendation are produced

### Requirement: Draft picks are scoreable against realized production

Draft-pick records SHALL be gradeable, once the season is complete, by the realized category
production of the player picked against the alternatives available at that pick.

#### Scenario: A logged pick is graded after the season

- **WHEN** a draft-pick record is graded against a completed season
- **THEN** the realized category production of the picked player is compared against the alternatives
  available at that pick

#### Scenario: Grading is reported per category

- **WHEN** a pick is graded
- **THEN** the outcome is reported per category rather than as a single aggregate value
