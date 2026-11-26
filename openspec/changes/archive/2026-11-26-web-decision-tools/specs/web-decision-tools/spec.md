## ADDED Requirements

### Requirement: The wire-lines board prices only stated-threshold crossings

The wire-lines board SHALL list players whose banked game line strictly exceeds
any of pts > 20, ast > 5, reb > 9, stl > 2, blk > 2, and SHALL show each listed
player's full line, usage-table minutes, and games left (0 for OUT). Players
landing exactly on a threshold SHALL be listed as near-misses, not board rows.
Rows SHALL order by pts, reb, ast, stl, blk descending, then player_id.

#### Scenario: An exactly-at-threshold line is a near-miss

- **WHEN** a player's banked line carries exactly 20 points and no other
  threshold crossing
- **THEN** he appears in the near-misses, not as a board row

#### Scenario: An OUT player prices zero games left

- **WHEN** a listed player's status is OUT
- **THEN** his games-left is 0

### Requirement: The fixture grid derives defense from what teams allowed

The fixture grid SHALL compute per-team allowed rates volume-weighted over
recent games, falling back to previous-season rates under 5 team-games, and
SHALL present direction-aware favorability with ranks and (defense, G/F/C)
position cells. Duplicate schedule rows for the same unordered team pair SHALL
collapse, with the official-game-id orientation winning.

#### Scenario: A thin-sample team falls back

- **WHEN** a team has fewer than 5 team-games of current-season data
- **THEN** its allowed rates come from the previous season

### Requirement: The tools API denies anonymous access

Every `/api/tools/*` route SHALL return 401 without a valid session. Sessions
SHALL be HMAC-signed cookies issued only after Yahoo OAuth identity
verification, and only to guids on the allow-list. `/api/health` SHALL remain
public and report `auth_ready`.

#### Scenario: Anonymous payload is refused

- **WHEN** a request hits `/api/tools/wire-lines` with no session cookie
- **THEN** the API returns 401

### Requirement: The committed board is the board of record

The committed `site/dist` SHALL serve as-is. Rebuilding the board at the same
`as_of` SHALL be treated as shifting every z-score (league-rate/usage drift
under a fixed date), so a re-base SHALL happen only through the explicit
rebuild flow, never as a silent regeneration.

#### Scenario: A same-as-of rebuild is not a silent replace

- **WHEN** the board generator is re-run at the committed snapshot's `as_of`
- **THEN** the shifted z-scores land only via the explicit rebuild flow, and
  the committed board remains what the repo served before

### Requirement: Production deploys ride git

A push to `main` SHALL deploy production through the git integration. The
deployed function SHALL be self-contained: the vendored package and the bundled
snapshot committed in-repo, with `includeFiles` kept as a single string glob.
The daily snapshot refresh SHALL be idempotent — a byte-identical snapshot
commits nothing and pushes nothing.

#### Scenario: Identical snapshot is a no-op

- **WHEN** the daily snapshot job copies a store byte-identical to the
  committed snapshot
- **THEN** it commits and pushes nothing
