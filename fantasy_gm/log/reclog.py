"""Append-only, structured recommendation log (D6).

One row per recommendation, capturing everything needed to (a) reproduce the call and
(b) later score it: creation time, as-of date, the inputs reference, the deciding
*perspective* (league / team / period / opponent), and the candidate/rank/score/
reasoning/confidence. The log exposes only ``append`` and reads — no update or delete in
normal use — so the published track record can never be quietly rewritten.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from fantasy_gm.engine.engine import Recommendation
from fantasy_gm.models import Perspective, ReconciliationMove, Signal

LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS recommendation_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT NOT NULL,
    as_of_date       TEXT NOT NULL,
    league_state_ref TEXT NOT NULL,
    league_id        TEXT NOT NULL,
    team_id          TEXT NOT NULL,
    period_index     INTEGER NOT NULL,
    opponent_team_id TEXT NOT NULL,
    candidate_id     TEXT NOT NULL,
    candidate_name   TEXT NOT NULL,
    rank             INTEGER NOT NULL,
    score            REAL NOT NULL,
    reasoning        TEXT NOT NULL,
    confidence       REAL NOT NULL
);
"""


@dataclass(frozen=True)
class LoggedRecommendation:
    id: int
    created_at: str
    as_of_date: str
    league_state_ref: str
    league_id: str
    team_id: str
    period_index: int
    opponent_team_id: str
    candidate_id: str
    candidate_name: str
    rank: int
    score: float
    reasoning: str
    confidence: float


class RecommendationLog:
    """Append-only façade over the ``recommendation_log`` table. No update/delete API."""

    def __init__(self, store):
        self.store = store
        self.conn = store.conn
        self.conn.executescript(LOG_SCHEMA)
        self.conn.commit()

    def append(self, recs: Iterable[Recommendation], created_at: str | None = None) -> int:
        created_at = created_at or datetime.now(UTC).isoformat()
        rows = [
            (created_at, r.as_of, r.league_state_ref, r.perspective.league_id,
             r.perspective.team_id, r.perspective.period_index,
             r.perspective.opponent_team_id, r.candidate_id, r.candidate_name,
             r.rank, r.score, r.reasoning, r.confidence)
            for r in recs
        ]
        self.conn.executemany(
            """INSERT INTO recommendation_log(
                   created_at, as_of_date, league_state_ref, league_id, team_id,
                   period_index, opponent_team_id, candidate_id, candidate_name,
                   rank, score, reasoning, confidence)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM recommendation_log").fetchone()
        return int(row["n"])

    def all(self) -> list[LoggedRecommendation]:
        return [
            LoggedRecommendation(**dict(r))
            for r in self.conn.execute("SELECT * FROM recommendation_log ORDER BY id")
        ]

    def for_perspective(
        self, league_id: str, team_id: str, as_of_date: str
    ) -> list[LoggedRecommendation]:
        return [
            LoggedRecommendation(**dict(r))
            for r in self.conn.execute(
                """SELECT * FROM recommendation_log
                   WHERE league_id = ? AND team_id = ? AND as_of_date = ?
                   ORDER BY rank""",
                (league_id, team_id, as_of_date),
            )
        ]


# --- Call-feed log (reframed record types) -----------------------------------

FEED_SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT NOT NULL,
    as_of_date       TEXT NOT NULL,
    league_id        TEXT NOT NULL,
    team_id          TEXT NOT NULL,
    period_index     INTEGER NOT NULL,
    opponent_team_id TEXT NOT NULL,
    subject_player   TEXT NOT NULL,
    subject_name     TEXT NOT NULL,
    owner_class      TEXT NOT NULL,
    signal_type      TEXT NOT NULL,
    evidence         TEXT NOT NULL,
    confidence       REAL NOT NULL,
    impact           REAL NOT NULL,
    relevance        REAL NOT NULL,
    strength         REAL NOT NULL,
    band             TEXT NOT NULL,
    affected_categories TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reconciliation_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at       TEXT NOT NULL,
    as_of_date       TEXT NOT NULL,
    league_id        TEXT NOT NULL,
    team_id          TEXT NOT NULL,
    period_index     INTEGER NOT NULL,
    opponent_team_id TEXT NOT NULL,
    add_id           TEXT NOT NULL,
    add_name         TEXT NOT NULL,
    drop_id          TEXT NOT NULL,
    drop_name        TEXT NOT NULL,
    line_of_play     TEXT NOT NULL,
    projected_impact TEXT NOT NULL,
    confidence       REAL NOT NULL,
    drops_unplayed   INTEGER NOT NULL
);
"""


class FeedLog:
    """Append-only log of the two call-feed record types (signal, reconciliation-move).
    No update/delete API — the track record cannot be quietly rewritten."""

    def __init__(self, store):
        self.store = store
        self.conn = store.conn
        self.conn.executescript(FEED_SCHEMA)
        self.conn.commit()

    def append_signals(
        self, signals: Iterable[Signal], perspective: Perspective, created_at: str | None = None
    ) -> int:
        created_at = created_at or datetime.now(UTC).isoformat()
        rows = [
            (created_at, s.as_of, perspective.league_id, perspective.team_id,
             perspective.period_index, perspective.opponent_team_id, s.subject_player,
             s.subject_name, s.owner_class, s.signal_type, s.evidence, s.confidence,
             s.impact, s.relevance, s.strength, s.band, json.dumps(list(s.affected_categories)))
            for s in signals
        ]
        self.conn.executemany(
            """INSERT INTO signal_log(
                   created_at, as_of_date, league_id, team_id, period_index, opponent_team_id,
                   subject_player, subject_name, owner_class, signal_type, evidence,
                   confidence, impact, relevance, strength, band, affected_categories)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def append_moves(
        self, moves: Iterable[ReconciliationMove], created_at: str | None = None
    ) -> int:
        created_at = created_at or datetime.now(UTC).isoformat()
        rows = [
            (created_at, m.as_of, m.perspective.league_id, m.perspective.team_id,
             m.perspective.period_index, m.perspective.opponent_team_id, m.add_id, m.add_name,
             m.drop_id, m.drop_name, m.line_of_play, json.dumps(m.projected_impact),
             m.confidence, int(m.drops_unplayed))
            for m in moves
        ]
        self.conn.executemany(
            """INSERT INTO reconciliation_log(
                   created_at, as_of_date, league_id, team_id, period_index, opponent_team_id,
                   add_id, add_name, drop_id, drop_name, line_of_play, projected_impact,
                   confidence, drops_unplayed)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def signal_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) n FROM signal_log").fetchone()["n"])

    def move_count(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) n FROM reconciliation_log").fetchone()["n"])

    def moves(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM reconciliation_log ORDER BY id")]

    def signals(self) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM signal_log ORDER BY id")]
