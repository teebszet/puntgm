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
from collections.abc import Iterable
from pathlib import Path

from fantasy_gm.models import (
    Availability,
    Game,
    LeagueState,
    Matchup,
    PlayerGameLog,
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
            """INSERT INTO games(game_id, season, game_date, home_team, away_team, home_pts, away_pts)
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
            """INSERT INTO player_logs(game_id, season, game_date, player_id, player_name, team, stats_json)
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
            """INSERT OR IGNORE INTO availability(player_id, status, known_from, source, confidence, note)
               VALUES (?,?,?,?,?,?)""",
            [(a.player_id, a.status, a.known_from, a.source, a.confidence, a.note) for a in records],
        )
        self.conn.commit()

    def record_provenance(self, subject: str, note: str, recorded_at: str) -> None:
        self.conn.execute(
            "INSERT INTO provenance(subject, note, recorded_at) VALUES (?,?,?)",
            (subject, note, recorded_at),
        )
        self.conn.commit()

    def create_league(
        self, league_id: str, name: str, season: str, cadence: str,
        categories: list[str], is_real: bool = False, seed: int | None = None,
    ) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO leagues(league_id, name, season, is_real, lineup_cadence, categories_json, seed)
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
            """INSERT OR REPLACE INTO roster_events(league_id, team_id, player_id, action, known_from)
               VALUES (?,?,?,?,?)""",
            (league_id, team_id, player_id, action, known_from),
        )
        self.conn.commit()

    def add_matchup(self, m: Matchup) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO matchups(league_id, period_index, period_start, period_end, team_a, team_b)
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
        """Per-category totals for a roster over the current period, using only games
        completed on or before ``as_of`` — point-in-time by construction."""
        tally = {c: 0.0 for c in categories}
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
                tally[c] += float(stats.get(c, 0.0))
        return tally

    def fantasy_points(self, stats: dict[str, float]) -> float:
        return _fantasy_points(stats)
