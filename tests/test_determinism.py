"""Guard: the synthetic season must be process-independent.

In-process tests share one PYTHONHASHSEED, so they can't catch a dependency on Python's
randomized str ``hash()``. This runs two subprocesses with different hash seeds and asserts
the generated schedule is identical — regression cover for the hash() determinism bug.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

_SNIPPET = """
import hashlib
from fantasy_gm.data.store import Store
from fantasy_gm.data.synthetic import seed_synthetic_season
s = Store(":memory:")
c = seed_synthetic_season(s, seed=7)
rows = s.conn.execute(
    "SELECT game_id, game_date, home_team, away_team FROM games ORDER BY game_id"
).fetchall()
digest = hashlib.sha256(repr([tuple(r) for r in rows]).encode()).hexdigest()
print(c["games"], digest)
"""


def _run(hashseed: str) -> str:
    env = {**os.environ, "PYTHONHASHSEED": hashseed}
    return subprocess.check_output(
        [sys.executable, "-c", _SNIPPET], cwd=_REPO, env=env
    ).decode().strip()


def test_synthetic_schedule_is_process_independent():
    assert _run("0") == _run("987654")
