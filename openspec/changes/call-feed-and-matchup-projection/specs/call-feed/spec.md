## ADDED Requirements

### Requirement: Typed, strength-graded signals

The system SHALL emit signals typed by kind (e.g. usage trend, efficiency trend, role/depth-chart
change, availability change, opponent move), each carrying a subject player, an owner class
(mine, opponent, free agent, tracked), supporting evidence, and a strength on a soft-to-strong
spectrum. Signal strength SHALL combine the signal's confidence, its impact on a contested
category, and its relevance to the deciding manager's build and stage of season.

#### Scenario: A signal carries type, evidence, and strength

- **WHEN** a signal is emitted for a player
- **THEN** it records the signal type, the subject player and owner class, the evidence, and a strength value
- **AND** the strength reflects confidence, impact on a contested category, and relevance

#### Scenario: A mirage stays soft

- **WHEN** a usage spike is not sustained and lacks a depth-chart cause
- **THEN** the resulting signal is graded soft rather than strong

#### Scenario: A sustained, causal, high-impact trend is strong

- **WHEN** a usage trend is sustained over multiple games, has an identifiable depth-chart cause, and swings a contested category for the deciding team
- **THEN** the resulting signal is graded strong

### Requirement: Relevance is situational and opponent-aware

Signal relevance SHALL depend on whose player the subject is, on which categories are live in the
deciding team's current matchup, and on the stage of the season; and the system SHALL derive an
opponent's targeted and conceded categories from the opponent's adds and drops.

#### Scenario: Relevance weights by owner and live categories

- **WHEN** the same trend occurs for a free agent who would swing a contested category and for a free agent who would only affect an already-safe category
- **THEN** the former signal is assigned higher relevance than the latter

#### Scenario: Opponent move reveals category strategy

- **WHEN** the opponent drops players strong in some categories and adds players strong in others
- **THEN** the system infers which categories the opponent is targeting and which it is conceding
- **AND** surfaces that as a signal affecting the deciding team's category plan

### Requirement: End-of-day reconciliation into candidate moves

After a day's games and before that day's waiver processing, the system SHALL summarise the day's
relevant signals into one or more candidate roster moves, each expressed as a required add and drop,
tied to a distinct line of play for the rest of the matchup, and annotated with the projected
per-category impact of making the move.

#### Scenario: Reconcile a day into a suggested move

- **WHEN** end-of-day reconciliation runs for a deciding team on date D
- **THEN** it produces at least one candidate move naming the add, the drop, and the line of play
- **AND** each candidate move states its projected effect on the relevant categories

#### Scenario: Dropping an unplayed player is flagged

- **WHEN** a candidate move would drop a player who has not yet played on date D for one who has
- **THEN** the move is flagged as the rare exception it is

### Requirement: Point-in-time feed

Every signal and reconciliation SHALL be computed from data known as of its date, reading only
through the as-of layer, so the feed for a date reflects only what was known then.

#### Scenario: Feed for a date uses only prior knowledge

- **WHEN** the feed is generated as of date D
- **THEN** every signal and candidate move derives only from data known on or before D
