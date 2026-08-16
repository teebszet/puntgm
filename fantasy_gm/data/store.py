"""Point-in-time SQLite store and as-of read layer (the repository).

Anti-lookahead contract (D3/D4):
  * Results (box scores) and game outcomes are event-dated by ``game_date``.
  * Availability and roster moves are effective-dated by ``known_from``.
  * Every as-of read filters ``<= as_of`` so no fact known only after D is returned.
  * The *schedule* (which teams play which dates) is a priori knowledge — it is known
    from season start, so upcoming-window schedule reads are intentionally NOT gated by
    as_of. Only outcomes/availability/rosters are.

The recommendation log lives in its own module but shares this database file.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
from collections.abc import Iterable
from pathlib import Path

from fantasy_gm.config import PERCENTAGE_CATEGORIES
from fantasy_gm.models import (
    ADP,
    Availability,
    ForwardRoster,
    Game,
    IncomingPlayer,
    LeagueState,
    Matchup,
    PlayerGameLog,
    PlayerPosition,
    Transaction,
    UsageRole,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    game_id     TEXT PRIMARY KEY,
    season      TEXT NOT NULL,
    game_date   TEXT NOT NULL,
    home_team   TEXT NOT NULL,
    away_team   TEXT NOT NULL,
    home_pts    INTEGER,
    away_pts    INTEGER
);
CREATE INDEX IF NOT EXISTS ix_games_date ON games(game_date);

CREATE TABLE IF NOT EXISTS player_logs (
    game_id     TEXT NOT NULL,
    season      TEXT NOT NULL,
    game_date   TEXT NOT NULL,
    player_id   TEXT NOT NULL,
    player_name TEXT NOT NULL,
    team        TEXT NOT NULL,
    stats_json  TEXT NOT NULL,
    PRIMARY KEY (game_id, player_id)
);
CREATE INDEX IF NOT EXISTS ix_logs_date ON player_logs(game_date);
CREATE INDEX IF NOT EXISTS ix_logs_player ON player_logs(player_id, game_date);

CREATE TABLE IF NOT EXISTS availability (
    player_id   TEXT NOT NULL,
    status      TEXT NOT NULL,
    known_from  TEXT NOT NULL,
    source      TEXT NOT NULL,
    confidence  REAL NOT NULL,
    note        TEXT DEFAULT '',
    PRIMARY KEY (player_id, known_from, source)
);
CREATE INDEX IF NOT EXISTS ix_avail_player ON availability(player_id, known_from);

CREATE TABLE IF NOT EXISTS provenance (
    subject     TEXT NOT NULL,
    note        TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leagues (
    league_id      TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    season         TEXT NOT NULL,
    is_real        INTEGER NOT NULL DEFAULT 0,
    lineup_cadence TEXT NOT NULL,
    categories_json TEXT NOT NULL,
    seed           INTEGER
);

CREATE TABLE IF NOT EXISTS teams (
    league_id TEXT NOT NULL,
    team_id   TEXT NOT NULL,
    team_name TEXT NOT NULL,
    PRIMARY KEY (league_id, team_id)
);

-- Roster membership as effective-dated add/drop events, so "roster as of D" is
-- reconstructable and no move is visible before it happened.
CREATE TABLE IF NOT EXISTS roster_events (
    league_id  TEXT NOT NULL,
    team_id    TEXT NOT NULL,
    player_id  TEXT NOT NULL,
    action     TEXT NOT NULL,        -- 'add' | 'drop'
    known_from TEXT NOT NULL,
    PRIMARY KEY (league_id, team_id, player_id, known_from)
);
CREATE INDEX IF NOT EXISTS ix_roster_league ON roster_events(league_id, known_from);

CREATE TABLE IF NOT EXISTS matchups (
    league_id    TEXT NOT NULL,
    period_index INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    team_a       TEXT NOT NULL,
    team_b       TEXT NOT NULL,
    PRIMARY KEY (league_id, period_index, team_a)
);

-- Effective-dated usage/role snapshots (D5): minutes, shot attempts, starter/bench,
-- and depth-chart position, so a role as of any date is reconstructable.
CREATE TABLE IF NOT EXISTS usage_role (
    player_id       TEXT NOT NULL,
    known_from      TEXT NOT NULL,
    minutes         REAL NOT NULL,
    fga             REAL NOT NULL,
    is_starter      INTEGER NOT NULL,
    depth_chart_pos INTEGER NOT NULL,
    PRIMARY KEY (player_id, known_from)
);
CREATE INDEX IF NOT EXISTS ix_usage_player ON usage_role(player_id, known_from);

-- --- Forward-season inputs (draft) -------------------------------------------
-- Everything below describes a season that has NOT been played. Existing tables are
-- all backward-looking (they need a game to exist first), so a draft tool has nowhere
-- to record "who is on which team next season". All are effective-dated by known_from,
-- so a draft-day read never sees a transaction reported afterwards.

-- Where a player sits going into a season: team and depth-chart position.
CREATE TABLE IF NOT EXISTS forward_roster (
    player_id       TEXT NOT NULL,
    season          TEXT NOT NULL,
    team            TEXT NOT NULL,
    depth_chart_pos INTEGER NOT NULL,
    role            TEXT NOT NULL DEFAULT '',
    known_from      TEXT NOT NULL,
    PRIMARY KEY (player_id, season, known_from)
);
CREATE INDEX IF NOT EXISTS ix_fwd_roster ON forward_roster(season, known_from);

-- Offseason moves that produced those rosters (trade, signing, waive, draft).
CREATE TABLE IF NOT EXISTS transactions (
    player_id   TEXT NOT NULL,
    season      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    from_team   TEXT DEFAULT '',
    to_team     TEXT DEFAULT '',
    known_from  TEXT NOT NULL,
    note        TEXT DEFAULT '',
    PRIMARY KEY (player_id, season, known_from, kind)
);
CREATE INDEX IF NOT EXISTS ix_txn_season ON transactions(season, known_from);

-- Players entering the league with no NBA game logs. They cannot be projected from
-- history (assumptions ledger A-DRAFT-6) and would otherwise be invisible to the
-- player pool, which derives from player_logs.
CREATE TABLE IF NOT EXISTS incoming_players (
    player_id   TEXT NOT NULL,
    season      TEXT NOT NULL,
    player_name TEXT NOT NULL,
    draft_pick  INTEGER,
    draft_team  TEXT DEFAULT '',
    known_from  TEXT NOT NULL,
    PRIMARY KEY (player_id, season)
);

-- Listed positions. Not derivable from box scores (which carry no position at all), so
-- without this the slot-assignment problem has no input and "depth chart position" can only
-- be read as rotation rank (assumptions ledger A-DRAFT-10).
CREATE TABLE IF NOT EXISTS player_positions (
    player_id   TEXT NOT NULL,
    position    TEXT NOT NULL,
    known_from  TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'nba',
    PRIMARY KEY (player_id, known_from, source)
);

-- Average draft position, for the opponent model. Sourced from the league platform
-- (Yahoo draft_analysis), so it is a market observation, not a projection.
CREATE TABLE IF NOT EXISTS adp (
    player_id   TEXT NOT NULL,
    season      TEXT NOT NULL,
    adp         REAL NOT NULL,
    adp_std     REAL,
    pct_drafted REAL,
    source      TEXT NOT NULL,
    known_from  TEXT NOT NULL,
    PRIMARY KEY (player_id, season, source, known_from)
);
CREATE INDEX IF NOT EXISTS ix_adp_season ON adp(season, known_from);
"""


def _fantasy_points(stats: dict[str, float]) -> float:
    """A simple, explainable production proxy (not the 9-cat z-score model)."""
    return (
        stats.get("pts", 0.0)
        + 1.2 * stats.get("reb", 0.0)
        + 1.5 * stats.get("ast", 0.0)
        + 3.0 * stats.get("stl", 0.0)
        + 3.0 * stats.get("blk", 0.0)
        + 1.0 * stats.get("fg3m", 0.0)
        - 1.0 * stats.get("tov", 0.0)
    )


class Store:
    """Owns the SQLite connection, schema, writes, and every as-of read."""

    def __init__(self, db_path: str | Path = ":memory:"):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- writes --------------------------------------------------------------
    def upsert_games(self, games: Iterable[Game]) -> None:
        self.conn.executemany(
            """INSERT INTO games(
                   game_id, season, game_date, home_team, away_team, home_pts, away_pts)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(game_id) DO UPDATE SET
                 home_pts=excluded.home_pts, away_pts=excluded.away_pts""",
            [
                (g.game_id, g.season, g.game_date, g.home_team, g.away_team, g.home_pts, g.away_pts)
                for g in games
            ],
        )
        self.conn.commit()

    def upsert_player_logs(self, logs: Iterable[PlayerGameLog]) -> None:
        self.conn.executemany(
            """INSERT INTO player_logs(
                   game_id, season, game_date, player_id, player_name, team, stats_json)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(game_id, player_id) DO UPDATE SET stats_json=excluded.stats_json""",
            [
                (lg.game_id, lg.season, lg.game_date, lg.player_id, lg.player_name, lg.team,
                 json.dumps(lg.stats))
                for lg in logs
            ],
        )
        self.conn.commit()

    def add_availability(self, records: Iterable[Availability]) -> None:
        self.conn.executemany(
            """INSERT OR IGNORE INTO availability(
                   player_id, status, known_from, source, confidence, note)
               VALUES (?,?,?,?,?,?)""",
            [(a.player_id, a.status, a.known_from, a.source, a.confidence, a.note)
             for a in records],
        )
        self.conn.commit()

    def record_provenance(self, subject: str, note: str, recorded_at: str) -> None:
        self.conn.execute(
            "INSERT INTO provenance(subject, note, recorded_at) VALUES (?,?,?)",
            (subject, note, recorded_at),
        )
        self.conn.commit()

    def clear_league(self, league_id: str) -> None:
        """Remove all state for a league so it can be re-created idempotently (re-drafting the
        same league_id must not pile new roster events on top of stale ones)."""
        for tbl in ("roster_events", "matchups", "teams", "leagues"):
            self.conn.execute(f"DELETE FROM {tbl} WHERE league_id = ?", (league_id,))
        self.conn.commit()

    def create_league(
        self, league_id: str, name: str, season: str, cadence: str,
        categories: list[str], is_real: bool = False, seed: int | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO leagues(
                   league_id, name, season, is_real, lineup_cadence, categories_json, seed)
               VALUES (?,?,?,?,?,?,?)""",
            (league_id, name, season, int(is_real), cadence, json.dumps(categories), seed),
        )
        self.conn.commit()

    def add_team(self, league_id: str, team_id: str, team_name: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO teams(league_id, team_id, team_name) VALUES (?,?,?)",
            (league_id, team_id, team_name),
        )
        self.conn.commit()

    def add_roster_event(
        self, league_id: str, team_id: str, player_id: str, action: str, known_from: str
    ) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO roster_events(
                   league_id, team_id, player_id, action, known_from)
               VALUES (?,?,?,?,?)""",
            (league_id, team_id, player_id, action, known_from),
        )
        self.conn.commit()

    def add_matchup(self, m: Matchup) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO matchups(
                   league_id, period_index, period_start, period_end, team_a, team_b)
               VALUES (?,?,?,?,?,?)""",
            (m.league_id, m.period_index, m.period_start, m.period_end, m.team_a, m.team_b),
        )
        self.conn.commit()

    # --- as-of reads (outcomes/availability/rosters gated by <= as_of) -------
    def results_asof(self, as_of: str, season: str | None = None) -> list[Game]:
        q = "SELECT * FROM games WHERE game_date <= ?"
        args: list = [as_of]
        if season:
            q += " AND season = ?"
            args.append(season)
        return [
            Game(r["game_id"], r["season"], r["game_date"], r["home_team"], r["away_team"],
                 r["home_pts"], r["away_pts"])
            for r in self.conn.execute(q, args)
        ]

    def player_logs_asof(
        self, as_of: str, player_id: str | None = None, since: str | None = None
    ) -> list[PlayerGameLog]:
        q = "SELECT * FROM player_logs WHERE game_date <= ?"
        args: list = [as_of]
        if player_id:
            q += " AND player_id = ?"
            args.append(player_id)
        if since:
            q += " AND game_date >= ?"
            args.append(since)
        q += " ORDER BY game_date"
        return [
            PlayerGameLog(r["game_id"], r["season"], r["game_date"], r["player_id"],
                          r["player_name"], r["team"], json.loads(r["stats_json"]))
            for r in self.conn.execute(q, args)
        ]

    def availability_asof(self, player_id: str, as_of: str) -> Availability | None:
        """Latest designation known on or before ``as_of`` (highest-confidence wins ties)."""
        row = self.conn.execute(
            """SELECT * FROM availability
               WHERE player_id = ? AND known_from <= ?
               ORDER BY known_from DESC, confidence DESC LIMIT 1""",
            (player_id, as_of),
        ).fetchone()
        if not row:
            return None
        return Availability(row["player_id"], row["status"], row["known_from"],
                            row["source"], row["confidence"], row["note"])

    def roster_asof(self, league_id: str, team_id: str, as_of: str) -> list[str]:
        """Reconstruct a team's roster from add/drop events known on or before ``as_of``."""
        rows = self.conn.execute(
            """SELECT player_id, action, known_from FROM roster_events
               WHERE league_id = ? AND team_id = ? AND known_from <= ?
               ORDER BY known_from""",
            (league_id, team_id, as_of),
        )
        current: set[str] = set()
        for r in rows:
            if r["action"] == "add":
                current.add(r["player_id"])
            elif r["action"] == "drop":
                current.discard(r["player_id"])
        return sorted(current)

    def roster_events_between(
        self, league_id: str, team_id: str, start: str, as_of: str
    ) -> list[dict]:
        """Add/drop events for a team with known_from in [start, as_of] — used to read an
        opponent's revealed category strategy (D6)."""
        return [
            {"player_id": r["player_id"], "action": r["action"], "known_from": r["known_from"]}
            for r in self.conn.execute(
                """SELECT player_id, action, known_from FROM roster_events
                   WHERE league_id = ? AND team_id = ? AND known_from >= ? AND known_from <= ?
                   ORDER BY known_from""",
                (league_id, team_id, start, as_of),
            )
        ]

    def team_ids(self, league_id: str) -> list[str]:
        return [
            r["team_id"]
            for r in self.conn.execute(
                "SELECT team_id FROM teams WHERE league_id = ? ORDER BY team_id", (league_id,)
            )
        ]

    def league_meta(self, league_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM leagues WHERE league_id = ?", (league_id,)
        ).fetchone()

    def active_matchup(self, league_id: str, as_of: str) -> Matchup | None:
        row = self.conn.execute(
            """SELECT * FROM matchups
               WHERE league_id = ? AND period_start <= ? AND period_end >= ?
               ORDER BY period_index LIMIT 1""",
            (league_id, as_of, as_of),
        ).fetchone()
        if not row:
            return None
        return Matchup(row["league_id"], row["period_index"], row["period_start"],
                       row["period_end"], row["team_a"], row["team_b"])

    def matchup_for_team(self, league_id: str, team_id: str, as_of: str) -> Matchup | None:
        row = self.conn.execute(
            """SELECT * FROM matchups
               WHERE league_id = ? AND period_start <= ? AND period_end >= ?
                 AND (team_a = ? OR team_b = ?)
               ORDER BY period_index LIMIT 1""",
            (league_id, as_of, as_of, team_id, team_id),
        ).fetchone()
        if not row:
            return None
        return Matchup(row["league_id"], row["period_index"], row["period_start"],
                       row["period_end"], row["team_a"], row["team_b"])

    # --- schedule (a priori — NOT gated by as_of) ----------------------------
    def schedule_in_window(self, start: str, end: str, season: str | None = None) -> list[Game]:
        """Games scheduled within [start, end]. Schedule is public/preseason knowledge,
        so this is intentionally not filtered by an as-of date."""
        q = "SELECT * FROM games WHERE game_date >= ? AND game_date <= ?"
        args: list = [start, end]
        if season:
            q += " AND season = ?"
            args.append(season)
        return [
            Game(r["game_id"], r["season"], r["game_date"], r["home_team"], r["away_team"],
                 r["home_pts"], r["away_pts"])
            for r in self.conn.execute(q, args)
        ]

    def games_in_window_for_team(self, team: str, start: str, end: str) -> int:
        row = self.conn.execute(
            """SELECT COUNT(*) AS n FROM games
               WHERE game_date >= ? AND game_date <= ? AND (home_team = ? OR away_team = ?)""",
            (start, end, team, team),
        ).fetchone()
        return int(row["n"])

    # --- composed point-in-time league state ---------------------------------
    def player_universe(self, season: str, as_of: str | None = None) -> list[tuple[str, str, str]]:
        """(player_id, player_name, team) for players seen this season (optionally as-of)."""
        q = "SELECT player_id, player_name, team FROM player_logs WHERE season = ?"
        args: list = [season]
        if as_of:
            q += " AND game_date <= ?"
            args.append(as_of)
        q += " GROUP BY player_id ORDER BY player_id"
        return [(r["player_id"], r["player_name"], r["team"]) for r in self.conn.execute(q, args)]

    def league_state_asof(self, league_id: str, as_of: str) -> LeagueState:
        meta = self.league_meta(league_id)
        if meta is None:
            raise KeyError(f"unknown league {league_id!r}")
        categories = json.loads(meta["categories_json"])
        rosters = {tid: self.roster_asof(league_id, tid, as_of) for tid in self.team_ids(league_id)}
        active = self.active_matchup(league_id, as_of)
        tally: dict[str, dict[str, float]] = {}
        if active is not None:
            for tid in (active.team_a, active.team_b):
                tally[tid] = self._team_category_tally(
                    rosters.get(tid, []), active.period_start, as_of, categories
                )
        return LeagueState(
            league_id=league_id,
            as_of=as_of,
            lineup_cadence=meta["lineup_cadence"],
            categories=categories,
            is_real=bool(meta["is_real"]),
            rosters=rosters,
            active_matchup=active,
            category_tally=tally,
        )

    def _team_category_tally(
        self, player_ids: list[str], period_start: str, as_of: str, categories: list[str]
    ) -> dict[str, float]:
        """Per-category totals for a roster over the current period, using only games in
        [period_start, as_of]. Counting cats are summed; percentage cats (A8) are
        volume-weighted (Σmakes / Σattempts), never a sum of per-game percentages."""
        tally = {c: 0.0 for c in categories}
        # component accumulators for percentage categories: {cat: [makes, attempts]}
        comps = {c: [0.0, 0.0] for c in categories if c in PERCENTAGE_CATEGORIES}
        if not player_ids:
            return tally
        placeholders = ",".join("?" for _ in player_ids)
        rows = self.conn.execute(
            f"""SELECT stats_json FROM player_logs
                WHERE player_id IN ({placeholders})
                  AND game_date >= ? AND game_date <= ?""",
            [*player_ids, period_start, as_of],
        )
        for r in rows:
            stats = json.loads(r["stats_json"])
            for c in categories:
                if c in PERCENTAGE_CATEGORIES:
                    mk, at = PERCENTAGE_CATEGORIES[c]
                    comps[c][0] += float(stats.get(mk, 0.0))
                    comps[c][1] += float(stats.get(at, 0.0))
                else:
                    tally[c] += float(stats.get(c, 0.0))
        for c, (makes, attempts) in comps.items():
            tally[c] = (makes / attempts) if attempts > 0 else 0.0
        return tally

    def category_totals(
        self, player_ids: list[str], start: str, end: str, categories: list[str]
    ) -> dict[str, float]:
        """Actual per-category totals for a set of players over [start, end], using real
        box scores (NOT gated by as-of) — for replay grading of a suggested move."""
        return self._team_category_tally(player_ids, start, end, categories)

    def matchup_by_period(self, league_id: str, period_index: int) -> Matchup | None:
        row = self.conn.execute(
            """SELECT * FROM matchups WHERE league_id = ? AND period_index = ?
               ORDER BY team_a LIMIT 1""",
            (league_id, period_index),
        ).fetchone()
        if not row:
            return None
        return Matchup(row["league_id"], row["period_index"], row["period_start"],
                       row["period_end"], row["team_a"], row["team_b"])

    def fantasy_points(self, stats: dict[str, float]) -> float:
        return _fantasy_points(stats)

    # --- usage / role (effective-dated) --------------------------------------
    def add_usage_role(self, records: Iterable[UsageRole]) -> None:
        self.conn.executemany(
            """INSERT OR REPLACE INTO usage_role(
                   player_id, known_from, minutes, fga, is_starter, depth_chart_pos)
               VALUES (?,?,?,?,?,?)""",
            [(u.player_id, u.known_from, u.minutes, u.fga, int(u.is_starter),
              u.depth_chart_pos) for u in records],
        )
        self.conn.commit()

    def usage_role_asof(self, player_id: str, as_of: str) -> UsageRole | None:
        row = self.conn.execute(
            """SELECT * FROM usage_role WHERE player_id = ? AND known_from <= ?
               ORDER BY known_from DESC LIMIT 1""",
            (player_id, as_of),
        ).fetchone()
        if not row:
            return None
        return UsageRole(row["player_id"], row["known_from"], row["minutes"], row["fga"],
                         bool(row["is_starter"]), row["depth_chart_pos"])

    def usage_role_history(self, player_id: str, as_of: str) -> list[UsageRole]:
        return [
            UsageRole(r["player_id"], r["known_from"], r["minutes"], r["fga"],
                      bool(r["is_starter"]), r["depth_chart_pos"])
            for r in self.conn.execute(
                """SELECT * FROM usage_role WHERE player_id = ? AND known_from <= ?
                   ORDER BY known_from""",
                (player_id, as_of),
            )
        ]

    # --- production distributions (mean + consistency), as of a date ---------
    def player_distribution_with_n(
        self, player_id: str, as_of: str, categories: list[str], window: int | None = None
    ) -> tuple[dict[str, tuple[float, float]], int]:
        """Same as ``player_distribution`` but also returns the sample size used, so callers
        can account for uncertainty in the estimated mean (not just game-to-game spread)."""
        logs = self.player_logs_asof(as_of, player_id=player_id)
        if window is not None:
            logs = logs[-window:]
        out: dict[str, tuple[float, float]] = {}
        for c in categories:
            vals = [lg.stats.get(c, 0.0) for lg in logs]
            if not vals:
                out[c] = (0.0, 0.0)
            elif len(vals) == 1:
                out[c] = (vals[0], 0.0)
            else:
                out[c] = (statistics.fmean(vals), statistics.pstdev(vals))
        return out, len(logs)

    def player_distribution(
        self, player_id: str, as_of: str, categories: list[str], window: int | None = None
    ) -> dict[str, tuple[float, float]]:
        """Per-category (mean, stdev) from games known on or before ``as_of``.
        Stdev is the consistency measure; a single game yields stdev 0."""
        logs = self.player_logs_asof(as_of, player_id=player_id)
        if window is not None:
            logs = logs[-window:]
        out: dict[str, tuple[float, float]] = {}
        for c in categories:
            vals = [lg.stats.get(c, 0.0) for lg in logs]
            if not vals:
                out[c] = (0.0, 0.0)
            elif len(vals) == 1:
                out[c] = (vals[0], 0.0)
            else:
                out[c] = (statistics.fmean(vals), statistics.pstdev(vals))
        return out

    # --- bulk as-of reads (pool-wide model fitting) --------------------------

    def player_game_stream_asof(
        self, as_of: str, since: str | None = None, season: str | None = None
    ) -> list[dict]:
        """Every player-game known on or before ``as_of``, with minutes attached.

        The per-player readers above are the right shape for one decision; fitting a model
        over the whole pool with them costs one query per player. This is the same as-of
        contract in one pass: ``{player_id, game_date, team, minutes, stats}``, ordered by
        player then date.

        Minutes live in ``usage_role`` (box scores carry no MIN), joined on the game date
        the snapshot was taken from. Players are kept even when the join misses, with
        ``minutes`` None, so a caller can tell "did not play" from "minutes unknown".
        """
        q = """SELECT l.player_id, l.game_date, l.team, l.stats_json, u.minutes
               FROM player_logs l
               LEFT JOIN usage_role u
                 ON u.player_id = l.player_id AND u.known_from = l.game_date
               WHERE l.game_date <= ?"""
        args: list = [as_of]
        if since:
            q += " AND l.game_date >= ?"
            args.append(since)
        if season:
            q += " AND l.season = ?"
            args.append(season)
        q += " ORDER BY l.player_id, l.game_date"
        return [
            {
                "player_id": r["player_id"],
                "game_date": r["game_date"],
                "team": r["team"],
                "minutes": None if r["minutes"] is None else float(r["minutes"]),
                "stats": json.loads(r["stats_json"]),
            }
            for r in self.conn.execute(q, args)
        ]

    def player_team(self, player_id: str, as_of: str) -> str | None:
        row = self.conn.execute(
            """SELECT team FROM player_logs WHERE player_id = ? AND game_date <= ?
               ORDER BY game_date DESC LIMIT 1""",
            (player_id, as_of),
        ).fetchone()
        return row["team"] if row else None

    def remaining_games_for_team(self, team: str, after: str, end: str) -> int:
        """Scheduled games for an NBA team strictly after ``after`` through ``end``
        (a priori schedule — not gated by as-of)."""
        row = self.conn.execute(
            """SELECT COUNT(*) AS n FROM games
               WHERE game_date > ? AND game_date <= ? AND (home_team = ? OR away_team = ?)""",
            (after, end, team, team),
        ).fetchone()
        return int(row["n"])

    # --- forward-season inputs (draft) --------------------------------------
    # Reads take the latest record known on or before ``as_of``, mirroring
    # ``usage_role_asof``: an offseason fact reported after draft day is invisible.

    def add_forward_roster(self, records: Iterable[ForwardRoster]) -> None:
        self.conn.executemany(
            """INSERT OR REPLACE INTO forward_roster(
                   player_id, season, team, depth_chart_pos, role, known_from)
               VALUES (?,?,?,?,?,?)""",
            [(r.player_id, r.season, r.team, r.depth_chart_pos, r.role, r.known_from)
             for r in records],
        )
        self.conn.commit()

    def forward_roster_asof(
        self, player_id: str, season: str, as_of: str
    ) -> ForwardRoster | None:
        row = self.conn.execute(
            """SELECT * FROM forward_roster
               WHERE player_id = ? AND season = ? AND known_from <= ?
               ORDER BY known_from DESC LIMIT 1""",
            (player_id, season, as_of),
        ).fetchone()
        if not row:
            return None
        return ForwardRoster(row["player_id"], row["season"], row["team"],
                             row["depth_chart_pos"], row["known_from"], row["role"])

    def add_player_positions(self, records: Iterable[PlayerPosition]) -> None:
        self.conn.executemany(
            """INSERT OR REPLACE INTO player_positions(
                   player_id, position, known_from, source)
               VALUES (?,?,?,?)""",
            [(p.player_id, p.position, p.known_from, p.source) for p in records],
        )
        self.conn.commit()

    def player_positions_asof(
        self, as_of: str, source: str | None = None
    ) -> dict[str, PlayerPosition]:
        """Latest listed position per player known on or before ``as_of``.

        Players with no listed position are simply absent, like ADP — the caller decides what
        an unpositioned player means rather than inheriting a slot they may not be eligible for.
        """
        sql = "SELECT * FROM player_positions WHERE known_from <= ?"
        params: list = [as_of]
        if source is not None:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY known_from"
        out: dict[str, PlayerPosition] = {}
        for r in self.conn.execute(sql, params):
            out[r["player_id"]] = PlayerPosition(r["player_id"], r["position"],
                                                 r["known_from"], r["source"])
        return out

    def add_transactions(self, records: Iterable[Transaction]) -> None:
        self.conn.executemany(
            """INSERT OR REPLACE INTO transactions(
                   player_id, season, kind, from_team, to_team, known_from, note)
               VALUES (?,?,?,?,?,?,?)""",
            [(t.player_id, t.season, t.kind, t.from_team, t.to_team, t.known_from, t.note)
             for t in records],
        )
        self.conn.commit()

    def transactions_asof(
        self, season: str, as_of: str, player_id: str | None = None
    ) -> list[Transaction]:
        sql = """SELECT * FROM transactions WHERE season = ? AND known_from <= ?"""
        params: list = [season, as_of]
        if player_id is not None:
            sql += " AND player_id = ?"
            params.append(player_id)
        sql += " ORDER BY known_from, player_id"
        return [
            Transaction(r["player_id"], r["season"], r["kind"], r["known_from"],
                        r["from_team"], r["to_team"], r["note"])
            for r in self.conn.execute(sql, params)
        ]

    def add_incoming_players(self, records: Iterable[IncomingPlayer]) -> None:
        self.conn.executemany(
            """INSERT OR REPLACE INTO incoming_players(
                   player_id, season, player_name, draft_pick, draft_team, known_from)
               VALUES (?,?,?,?,?,?)""",
            [(p.player_id, p.season, p.player_name, p.draft_pick, p.draft_team, p.known_from)
             for p in records],
        )
        self.conn.commit()

    def incoming_players_asof(self, season: str, as_of: str) -> list[IncomingPlayer]:
        return [
            IncomingPlayer(r["player_id"], r["season"], r["player_name"], r["known_from"],
                           r["draft_pick"], r["draft_team"])
            for r in self.conn.execute(
                """SELECT * FROM incoming_players WHERE season = ? AND known_from <= ?
                   ORDER BY draft_pick IS NULL, draft_pick, player_id""",
                (season, as_of),
            )
        ]

    def add_adp(self, records: Iterable[ADP]) -> None:
        self.conn.executemany(
            """INSERT OR REPLACE INTO adp(
                   player_id, season, adp, adp_std, pct_drafted, source, known_from)
               VALUES (?,?,?,?,?,?,?)""",
            [(a.player_id, a.season, a.adp, a.adp_std, a.pct_drafted, a.source, a.known_from)
             for a in records],
        )
        self.conn.commit()

    def adp_asof(
        self, season: str, as_of: str, source: str | None = None
    ) -> dict[str, ADP]:
        """Latest ADP per player known on or before ``as_of``.

        Players with no ADP are simply absent — the caller must decide what an
        undrafted-in-the-market player is worth rather than inheriting a default.
        """
        sql = """SELECT * FROM adp WHERE season = ? AND known_from <= ?"""
        params: list = [season, as_of]
        if source is not None:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY known_from"
        out: dict[str, ADP] = {}
        for r in self.conn.execute(sql, params):
            out[r["player_id"]] = ADP(r["player_id"], r["season"], r["adp"], r["source"],
                                      r["known_from"], r["adp_std"], r["pct_drafted"])
        return out

    def draft_pool_asof(self, season: str, as_of: str) -> list[str]:
        """Every player draftable for ``season``: those with prior NBA logs plus
        incoming players who have none."""
        seen = {
            r["player_id"]
            for r in self.conn.execute(
                "SELECT DISTINCT player_id FROM player_logs WHERE game_date <= ?", (as_of,)
            )
        }
        seen.update(p.player_id for p in self.incoming_players_asof(season, as_of))
        return sorted(seen)
