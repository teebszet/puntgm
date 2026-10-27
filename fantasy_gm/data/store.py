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
from datetime import date, timedelta
from pathlib import Path

from fantasy_gm.config import FORWARD_SEASON, PERCENTAGE_CATEGORIES
from fantasy_gm.models import (
    ADP,
    Availability,
    ForwardRoster,
    Game,
    IncomingPlayer,
    LeagueState,
    Matchup,
    NewsEvent,
    PlayerGameLog,
    PlayerPosition,
    Transaction,
    UsageRole,
)

# Platform slots that accrue nothing until an explicit move slates the player:
# bench and IL (same set Projector.INACTIVE_SLOTS prices; kept local so the data
# layer does not import the engine).
NON_ACCRUING_SLOTS = frozenset({"BN", "IL", "IR", "NA", "INACT"})

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

-- Dated news events (injury designations, rotation notes, roster moves, wire items),
-- matched to store players when the item names one. Same ingest discipline as
-- availability: every row carries its source and the date the item is dated, and an
-- as-of read never sees a row known only later. A wire item that names no pool player
-- is still stored (league context) under the empty player id — it can flag nothing.
CREATE TABLE IF NOT EXISTS news_events (
    source       TEXT NOT NULL,
    published_on TEXT NOT NULL,
    player_id    TEXT NOT NULL DEFAULT '',
    name         TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL,
    headline     TEXT NOT NULL,
    note         TEXT DEFAULT '',
    url          TEXT DEFAULT '',
    known_from   TEXT NOT NULL,
    PRIMARY KEY (source, published_on, player_id, headline)
);
CREATE INDEX IF NOT EXISTS ix_news_player ON news_events(player_id, published_on);

-- The platform's scheduled lineup: which slot each rostered player occupies (PG..Util,
-- BN, IL), as Yahoo serves it. Effective-dated like roster_events so a re-slot is
-- reconstructable, and the projector can price the *lineup* rather than the full
-- roster — a bench or IL player accrues nothing until he is actually slotted.
CREATE TABLE IF NOT EXISTS lineup_slots (
    league_id  TEXT NOT NULL,
    team_id    TEXT NOT NULL,
    player_id  TEXT NOT NULL,
    slot       TEXT NOT NULL,
    known_from TEXT NOT NULL,
    PRIMARY KEY (league_id, team_id, player_id, known_from)
);
CREATE INDEX IF NOT EXISTS ix_lineup_team ON lineup_slots(league_id, team_id, known_from);
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
        # (player, as_of, window) -> participation rate. Read-only derived data on a store
        # whose logs are static during a replay; cleared by upsert_player_logs.
        self._participation_cache: dict[tuple[str, str, int], float] = {}
        # (season, pool_size) -> league percentage rates; see valuation.league_percentage_rates
        self._pct_rate_cache: dict[tuple[str, int], dict[str, float]] = {}

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
        self._participation_cache.clear()  # appearances just changed
        self._pct_rate_cache.clear()       # and so did the league shooting rates

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

    def upsert_lineups(
        self, league_id: str, rows: Iterable[tuple[str, str, str]], known_from: str
    ) -> int:
        """Store one platform lineup read as a dated slot snapshot; returns rows changed.

        A read owns its ``known_from`` date per team: the snapshot is replaced
        wholesale (slot rows absent from the new payload are deleted, changed slots
        updated), so a lineup edited between two reads of the same date cannot stay
        stale in the fold. Idempotent: re-storing an identical read changes nothing
        and returns 0, which keeps the refresh wires quiet on a shape-identical pass.
        """
        rows = list(rows)
        old = {
            (r["team_id"], r["player_id"]): r["slot"]
            for r in self.conn.execute(
                """SELECT team_id, player_id, slot FROM lineup_slots
                   WHERE league_id = ? AND known_from = ?""",
                (league_id, known_from),
            )
        }
        new = {(t, p): slot for t, p, slot in rows}
        changed = sum(1 for k, v in new.items() if old.get(k) != v)
        changed += sum(1 for k in old if k not in new)
        for team_id in {t for t, _p, _s in rows}:
            self.conn.execute(
                """DELETE FROM lineup_slots
                   WHERE league_id = ? AND known_from = ? AND team_id = ?""",
                (league_id, known_from, team_id),
            )
        self.conn.executemany(
            """INSERT INTO lineup_slots(
                   league_id, team_id, player_id, slot, known_from)
               VALUES (?,?,?,?,?)""",
            [(league_id, t, p, slot, known_from) for t, p, slot in rows],
        )
        self.conn.commit()
        return changed

    def lineup_asof(self, league_id: str, team_id: str, as_of: str) -> dict[str, str]:
        """{player_id: slot} for a team's most recent lineup read on/before ``as_of``.

        Empty when the platform lineup has never been read for the team — callers treat
        that as 'no lineup knowledge' and fall back to the full roster.
        """
        rows = self.conn.execute(
            """SELECT player_id, slot, known_from FROM lineup_slots
               WHERE league_id = ? AND team_id = ? AND known_from <= ?
               ORDER BY known_from""",
            (league_id, team_id, as_of),
        ).fetchall()
        return {r["player_id"]: r["slot"] for r in rows}

    def scheduled_active_games(
        self, league_id: str, team_id: str, as_of: str, period_end: str
    ) -> dict[str, int] | None:
        """Per-player remaining games priced off per-date scheduled lineups.

        Yahoo's own games-remaining counter counts, for each remaining date of the
        matchup week, the players in that date's *starting* slots whose NBA team
        plays that date — bench and IL never count (verified live 2026-10-26: the
        per-day sum over Yahoo's own scheduled-lineup reads reproduced Yahoo's
        40/41 exactly, where the current-lineup-for-the-whole-week model gave
        30/33 and a bench-inclusive model gave 40/42). A manager who re-slots daily
        to maximise games must have each player priced on exactly the days he is
        scheduled to be slotted — not the whole window, and not zero.

        Lineup coverage per date comes from date-scoped lineup reads stored with
        ``known_from = covered date``; a date's effective lineup is the
        ``lineup_asof`` fold on that date (a scheduled change takes effect on its
        own date; where nothing is scheduled the previous lineup carries forward).

        Returns ``{player_id: remaining games}`` over the team's rostered players,
        or ``None`` when any remaining date has no lineup read covering it exactly
        — callers then fall back to the current-lineup behaviour rather than
        silently mis-pricing the days in between.
        """
        dates: list[str] = []
        d = date.fromisoformat(as_of)
        end = date.fromisoformat(period_end)
        while d <= end:
            dates.append(d.isoformat())
            d += timedelta(days=1)
        reads = {
            r["known_from"]
            for r in self.conn.execute(
                "SELECT DISTINCT known_from FROM lineup_slots WHERE league_id = ?",
                (league_id,),
            )
        }
        if not dates or any(d not in reads for d in dates):
            return None
        # Remaining games per date, same played-ness rule as remaining_games_for_team:
        # a game on the as-of day itself counts only while it is still unscored.
        teams_by_date: dict[str, set[str]] = {}
        for r in self.conn.execute(
            """SELECT game_date, home_team, away_team FROM games
               WHERE game_date >= ? AND game_date <= ?
                 AND (game_date > ? OR (home_pts IS NULL AND away_pts IS NULL))""",
            (as_of, period_end, as_of),
        ):
            teams_by_date.setdefault(r["game_date"], set()).update(
                (r["home_team"], r["away_team"])
            )
        out: dict[str, int] = {}
        team_cache: dict[str, str | None] = {}
        for day in dates:
            playing = teams_by_date.get(day, set())
            for pid, slot in self.lineup_asof(league_id, team_id, day).items():
                if slot in NON_ACCRUING_SLOTS:
                    continue
                if pid not in team_cache:
                    team_cache[pid] = self.player_team(pid, as_of)
                nba_team = team_cache[pid]
                if nba_team and nba_team in playing:
                    out[pid] = out.get(pid, 0) + 1
        return out

    def rosters_asof(self, league_id: str, as_of: str) -> dict[str, list[str]]:
        """Rosters per team as of ``as_of``.

        Preferred source: the platform's latest full lineup read on/before ``as_of`` —
        every rostered player (bench and IL included) under a name-resolved id. Fantasy
        ownership moves daily, and nothing in the refresh wires writes ``roster_events``
        (that table is fed only by manual Yahoo imports), so an events fold goes stale:
        dropped players keep pricing into their old team and adds read as free agents
        (seen live 2026-10-26 — Mark Williams read as owned a week after he was dropped,
        and the wire pool offered players other teams had already claimed). Reads are
        full-league snapshots, so the roster set is the rows of the latest read, never a
        fold across reads. Falls back to the roster_events fold when no read exists yet.
        """
        read = self.conn.execute(
            """SELECT MAX(known_from) AS k FROM lineup_slots
               WHERE league_id = ? AND known_from <= ?""",
            (league_id, as_of),
        ).fetchone()["k"]
        if read is not None:
            rows = self.conn.execute(
                """SELECT team_id, player_id FROM lineup_slots
                   WHERE league_id = ? AND known_from = ?""",
                (league_id, read),
            ).fetchall()
            # A read owns the roster set only when it is a FULL-league snapshot; a
            # partial read (one team fetched by hand, a draft-era probe) must not
            # silently zero the teams it didn't cover.
            if {r["team_id"] for r in rows} >= set(self.team_ids(league_id)):
                rosters: dict[str, list[str]] = {tid: [] for tid in self.team_ids(league_id)}
                for r in rows:
                    rosters[r["team_id"]].append(r["player_id"])
                for ids in rosters.values():
                    ids.sort()
                return rosters
        return {tid: self.roster_asof(league_id, tid, as_of)
                for tid in self.team_ids(league_id)}

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

    def resolve_team_id(self, league_id: str, team_id: str) -> str | None:
        """Accept either the stored full id (``yahoo-478.l.25733.t.8``) or the short
        suffix (``t.8``) and return the stored form, or None when neither matches.

        The store keys teams by the full id, but humans and the CLI naturally say
        ``t.8`` — a raw short id reached every matchup/team lookup and came back
        "no active matchup", which read as missing projections rather than an id
        mismatch (found live, 2026-10-26).
        """
        ids = self.team_ids(league_id)
        if team_id in ids:
            return team_id
        # Suffix match rather than prefix reconstruction: the stored league key is
        # ``yahoo-478.l.25733`` but the stored team ids are ``478.l.25733.t.8`` —
        # they do not share a prefix, so ``league_id + "." + team_id`` never matches.
        matches = [tid for tid in ids if tid.endswith("." + team_id)]
        return matches[0] if len(matches) == 1 else None

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
        rosters = self.rosters_asof(league_id, as_of)
        active = self.active_matchup(league_id, as_of)
        # Every live matchup's teams get their banked tally, not just one representative
        # pair: ``active_matchup`` returns a single row, and keying the tally to it left
        # every other team's projection pricing zero banked points (found live 2026-10-25
        # — week 1 tallied only t.1/t.4, so t.8's projection read 0.0 across the board).
        live = self.conn.execute(
            """SELECT team_a, team_b, period_start FROM matchups
               WHERE league_id = ? AND period_start <= ? AND period_end >= ?""",
            (league_id, as_of, as_of),
        ).fetchall()
        tally: dict[str, dict[str, float]] = {}
        for row in live:
            for tid in (row["team_a"], row["team_b"]):
                tally[tid] = self._team_category_tally(
                    rosters.get(tid, []), row["period_start"], as_of, categories
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
        """The NBA team the player plays for as of ``as_of``.

        The forward-season row wins when one exists (an offseason move is a current fact
        the logs cannot express — the 2026-27 fold placed Kawhi Leonard on TOR while his
        last logged game is LAC), else the last logged team. Forward rows are known-from
        dated, so an earlier ``as_of`` never sees a later move and replays of logged
        seasons behave exactly as before."""
        row = self.conn.execute(
            """SELECT team FROM forward_roster
               WHERE player_id = ? AND season = ? AND known_from <= ?
               ORDER BY known_from DESC LIMIT 1""",
            (player_id, FORWARD_SEASON, as_of),
        ).fetchone()
        if row and row["team"]:
            return row["team"]
        row = self.conn.execute(
            """SELECT team FROM player_logs WHERE player_id = ? AND game_date <= ?
               ORDER BY game_date DESC LIMIT 1""",
            (player_id, as_of),
        ).fetchone()
        return row["team"] if row else None

    def latest_logged_season(self) -> str:
        """The most recent season with player logs — the ranked-data season.

        Week 1 of a fresh league season has no box scores yet, so every pool, valuation,
        and league-rate read that derives from logged games (wire, recommend) must read
        the last season's data; the league's own season stays what the schedule and
        rosters key on."""
        row = self.conn.execute("SELECT MAX(season) AS s FROM player_logs").fetchone()
        return row["s"] or ""

    def remaining_games_for_team(self, team: str, after: str, end: str) -> int:
        """Scheduled games for an NBA team still to be played from ``after`` through
        ``end`` (a priori schedule — not gated by as-of).

        Counted as DISTINCT (date, home, away) real games: the ESPN schedule wire keys
        ``esp-*`` ids while the LeagueGameLog backfill keys NBA gids, so once both load
        the same season a plain COUNT double-counts every game both wires carry.

        The ``after`` day itself counts when the game is still unscored: a game tonight
        is neither banked (``player_logs`` stop yesterday) nor remaining under a strict
        ``> after`` cut, so a same-day projection silently dropped the whole day's
        slate. Played-ness reads the stored final score — the ESPN wire marks a slate
        final as results land — so once a game finishes it leaves this count, and when
        the daily backfill stores its box scores it appears in the banked tally. The
        two buckets never overlap. (Any scored duplicate row marks the real game
        played, so a wire that carries scores settles it for its unscored twin.)
        """
        row = self.conn.execute(
            """SELECT COUNT(*) AS n FROM (
                   SELECT game_date, home_team, away_team
                   FROM games
                   WHERE game_date >= ? AND game_date <= ?
                     AND (home_team = ? OR away_team = ?)
                   GROUP BY game_date, home_team, away_team
                   HAVING MIN(game_date) > ?
                       OR SUM(home_pts IS NULL AND away_pts IS NULL) = COUNT(*)
               )""",
            (after, end, team, team, after),
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

    def forward_roster_team_asof(
        self, team: str, season: str, as_of: str
    ) -> list[ForwardRoster]:
        """Every player's latest-as-of roster row for one team, at that row's own date.

        The team read the promotion rule needs: a player whose latest effective-dated
        row places them elsewhere is excluded by the team filter even though their
        history was read — each player is read at their own latest ``known_from``.
        """
        rows = self.conn.execute(
            """SELECT fr.* FROM forward_roster fr
               WHERE fr.season = ? AND fr.team = ? AND fr.known_from <= ?
                 AND fr.known_from = (
                     SELECT MAX(known_from) FROM forward_roster fr2
                     WHERE fr2.player_id = fr.player_id
                       AND fr2.season = fr.season AND fr2.known_from <= ?)
               ORDER BY fr.depth_chart_pos, fr.player_id""",
            (season, team, as_of, as_of),
        ).fetchall()
        return [ForwardRoster(r["player_id"], r["season"], r["team"],
                              r["depth_chart_pos"], r["known_from"], r["role"])
                for r in rows]

    def forward_roster_map_asof(
        self, season: str, as_of: str
    ) -> dict[str, ForwardRoster]:
        """Every player's latest-as-of roster row for the season, keyed by player id.

        The whole-forward-roster read the roster-move diff needs: each player at their
        own latest ``known_from``, so a row written after ``as_of`` is invisible.
        """
        rows = self.conn.execute(
            """SELECT fr.* FROM forward_roster fr
               WHERE fr.season = ? AND fr.known_from <= ?
                 AND fr.known_from = (
                     SELECT MAX(known_from) FROM forward_roster fr2
                     WHERE fr2.player_id = fr.player_id
                       AND fr2.season = fr.season AND fr2.known_from <= ?)""",
            (season, as_of, as_of),
        ).fetchall()
        return {r["player_id"]: ForwardRoster(
                    r["player_id"], r["season"], r["team"],
                    r["depth_chart_pos"], r["known_from"], r["role"])
                for r in rows}

    def add_news_events(self, records: Iterable[NewsEvent]) -> int:
        """Insert news rows (INSERT OR REPLACE — the key is the item's identity).

        Returns the number of rows actually new, so an ingest pass can report what
        changed rather than recounting what it re-wrote (the refresh diff needs that).
        """
        before = self.conn.execute("SELECT COUNT(*) AS n FROM news_events").fetchone()["n"]
        self.conn.executemany(
            """INSERT OR REPLACE INTO news_events(
                   source, published_on, player_id, name, kind, headline, note, url,
                   known_from)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            [(r.source, r.published_on, r.player_id, r.name, r.kind, r.headline,
              r.note, r.url, r.known_from or r.published_on) for r in records],
        )
        self.conn.commit()
        after = self.conn.execute("SELECT COUNT(*) AS n FROM news_events").fetchone()["n"]
        return max(after - before, 0)

    def news_asof(
        self, player_ids: Iterable[str], as_of: str, *, per_player: int = 3
    ) -> dict[str, list[NewsEvent]]:
        """Latest ``per_player`` news rows per player, known on or before ``as_of``.

        Newest first within a player. A player with no rows maps to an empty list —
        "no news" is a real state, never a silent gap.
        """
        ids = list(dict.fromkeys(player_ids))
        if not ids:
            return {}
        out: dict[str, list[NewsEvent]] = {pid: [] for pid in ids}
        marks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT * FROM news_events WHERE player_id IN ({marks}) "  # noqa: S608
            "AND known_from <= ? ORDER BY player_id, published_on DESC, headline",
            (*ids, as_of),
        )
        for r in rows:
            bucket = out.get(r["player_id"])
            if bucket is not None and len(bucket) < per_player:
                bucket.append(NewsEvent(
                    source=r["source"], published_on=r["published_on"],
                    player_id=r["player_id"], name=r["name"], kind=r["kind"],
                    headline=r["headline"], note=r["note"], url=r["url"],
                    known_from=r["known_from"],
                ))
        return out

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
    def participation_rate(
        self, player_id: str, as_of: str, window: int = 5
    ) -> float | None:
        """Fraction of the player's team's last ``window`` scheduled games that the player
        actually appeared in, as of ``as_of``. ``None`` when the player has no team yet.

        A *scheduled* game is not a *played* game: measured on the real 2025-26 season the
        mean participation rate is **0.49** (rosterable pool 0.73, off-pool wire 0.45), so
        treating remaining team games as remaining player games overstates every projection
        — and overstates wire candidates roughly twice as much as rostered ones, which is
        exactly the bias that makes an engine recommend players who never take the floor.

        The raw trailing rate is deliberately *not* shrunk. Participation is strongly
        bimodal (66.6% of observations are 0/5 or 5/5), so pulling toward the mean lands in
        a region almost nobody occupies. Measured on held-out data, raw k/n beats Beta
        shrinkage and an empirical calibration map alike (MAE 0.177 vs 0.207 vs 0.509 for
        assume-1.0), with negligible bias.

        Known limitation: a player returning from injury has a trailing rate of 0 and is
        projected to contribute nothing until they appear. Point-in-time availability
        (``availability_asof``) is the layer meant to correct that, not this one.
        """
        key = (player_id, as_of, window)
        cached = self._participation_cache.get(key)
        if cached is not None:
            return cached
        team = self.player_team(player_id, as_of)
        if team is None:
            return None
        sched = [
            r["game_date"]
            for r in self.conn.execute(
                """SELECT DISTINCT game_date FROM games
                   WHERE game_date <= ? AND (home_team = ? OR away_team = ?)
                   ORDER BY game_date DESC LIMIT ?""",
                (as_of, team, team, window),
            )
        ]
        if not sched:
            return None
        row = self.conn.execute(
            f"""SELECT COUNT(DISTINCT game_date) n FROM player_logs
                WHERE player_id = ? AND game_date IN ({",".join("?" * len(sched))})""",
            (player_id, *sched),
        ).fetchone()
        rate = int(row["n"]) / len(sched)
        self._participation_cache[key] = rate
        return rate
