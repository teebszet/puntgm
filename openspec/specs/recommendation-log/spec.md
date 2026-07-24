# recommendation-log Specification

## Purpose
TBD - created by archiving change bootstrap-historical-data-and-engine. Update Purpose after archive.
## Requirements
### Requirement: Structured recommendation logging

The system SHALL persist every recommendation the engine produces as a structured record capturing creation time, as-of date, inputs reference, the deciding perspective, the candidate, rank, score, reasoning, and confidence.

#### Scenario: Recommendation is logged with full context

- **WHEN** the engine produces a recommendation
- **THEN** a log record is written containing created-at timestamp, as-of date, a reference to the league-state inputs, the perspective (league, team, scoring period, opponent), the candidate, its rank, its score, the reasoning string, and a confidence value

#### Scenario: Perspective identifies whose decision it was

- **WHEN** a recommendation record is read
- **THEN** it identifies which league, which team, which scoring period, and which opponent the recommendation was made for
- **AND** two recommendations differing only in perspective are distinguishable in the log

### Requirement: Append-only integrity

The recommendation log SHALL be append-only so that past recommendations cannot be silently modified or deleted.

#### Scenario: Existing records are immutable

- **WHEN** new recommendations are written
- **THEN** previously written records remain unchanged
- **AND** the system provides no operation to edit or delete an existing recommendation record in normal use

### Requirement: Reproducible from the log

A logged recommendation SHALL contain enough information to reproduce and later score the call.

#### Scenario: A record can be replayed

- **WHEN** a log record is read
- **THEN** its as-of date, inputs reference, and perspective are sufficient to re-run the engine and obtain the same recommendation

