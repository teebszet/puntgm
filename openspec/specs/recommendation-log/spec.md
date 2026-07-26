# recommendation-log Specification

## Purpose
TBD - created by archiving change bootstrap-historical-data-and-engine. Update Purpose after archive.
## Requirements
### Requirement: Structured recommendation logging

The system SHALL persist the call feed as structured records of two kinds, capturing creation
time, as-of date, the deciding perspective (league, team, period, opponent), and an inputs
reference: **signal** records (subject player, owner class, signal type, evidence, strength,
affected categories) and **reconciliation-move** records (the add, the drop, the line of play,
and the projected per-category impact).

#### Scenario: A signal is logged with full context

- **WHEN** the feed emits a signal
- **THEN** a signal record is written with created-at, as-of date, perspective, subject player, owner class, signal type, evidence, strength, and affected categories

#### Scenario: A reconciliation move is logged with projected impact

- **WHEN** end-of-day reconciliation proposes a move
- **THEN** a reconciliation-move record is written with created-at, as-of date, perspective, the add, the drop, the line of play, and the projected per-category impact

### Requirement: Append-only integrity

The recommendation log SHALL be append-only so that past recommendations cannot be silently modified or deleted.

#### Scenario: Existing records are immutable

- **WHEN** new recommendations are written
- **THEN** previously written records remain unchanged
- **AND** the system provides no operation to edit or delete an existing recommendation record in normal use

### Requirement: Reproducible from the log

A logged record SHALL contain enough information to reproduce the call and, for a move, to score
it against what actually happened.

#### Scenario: A record can be replayed

- **WHEN** a record is read
- **THEN** its as-of date, perspective, and inputs reference are sufficient to re-run the engine and obtain the same signal or move

### Requirement: Scoreable calls

Each reconciliation-move record SHALL be gradable in replay by its realized category impact
versus standing pat, and projections SHALL be gradable for calibration, so the log constitutes a
verifiable track record.

#### Scenario: A move is graded by realized impact

- **WHEN** a logged move ("drop A, add B on date D") is scored in replay
- **THEN** B's and A's actual production over the remainder of the period is applied to the category tallies
- **AND** the move is scored by whether it flipped a contested category or widened a margin versus not moving

#### Scenario: Projection calibration is measurable

- **WHEN** categories the system labeled safe are checked against actual period results
- **THEN** a calibration measure is produced from how often "safe" labels held

