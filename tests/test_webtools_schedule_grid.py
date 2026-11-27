"""Schedule-grid tests — NBA teams × dates off the shared schedule + defense model.

The grid is a join of two pieces this repo already proves independently: the
schedule window (``fixture_grid._schedule``, dedupe-tested there) and the team
defense model (``fixture_grid.team_defense``, ground-truth-tested there). These
tests pin the join: every scheduled team renders, every cell mirrors one stored
game, the league-games row equals the distinct slate per date, and the composite
/ per-cat favors echo the defense model verbatim.
"""

from __future__ import annotations

import pytest

from fantasy_gm.data.store import Store
from fantasy_gm.webtools import schedule_grid_payload, team_defense

MID = "2025-11-11"  # the conftest fixture's mid-season date


def test_rows_cover_every_scheduled_team(fx):
    payload = schedule_grid_payload(fx.store, MID, days=14)
    dates = payload["dates"]
    assert len(dates) == 14 and dates[0] == MID

    schedule: dict[str, set[tuple[str, str]]] = {}
    for r in fx.store.conn.execute(
            "SELECT game_date, home_team, away_team FROM games "
            "WHERE game_date >= ? AND game_date <= ?", (MID, dates[-1])):
        schedule.setdefault(r["game_date"], set()).add((r["home_team"], r["away_team"]))

    scheduled_teams = {t for day in schedule.values() for pair in day for t in pair}
    assert scheduled_teams <= {t["team"] for t in payload["teams"]}
    for row in payload["teams"]:
        assert row["games"] == len(row["cells"])
        for date, cell in row["cells"].items():
            assert any(row["team"] in pair for pair in schedule.get(date, ())), \
                f"{row['team']} {date} vs {cell['opponent']} not on the schedule"
    assert payload["league_games_by_date"] == [len(schedule.get(d, ())) for d in dates]


def test_cell_favor_echoes_the_defense_model(fx):
    defense = team_defense(fx.store, MID)
    payload = schedule_grid_payload(fx.store, MID, days=14)
    checked = 0
    for row in payload["teams"]:
        for cell in row["cells"].values():
            favor = defense.favor.get(cell["opponent"], {})
            if not favor:
                assert cell["favor"] is None and cell["favor_by_cat"] == {}
                continue
            assert cell["favor"] == pytest.approx(
                sum(favor.values()) / len(favor), abs=1e-3)
            assert cell["favor_by_cat"] == {
                c: round(v, 2) for c, v in sorted(favor.items())}
            checked += 1
    assert checked, "the fixture window must face opponents with defense data"


def test_teams_without_games_still_render_as_rows(fx):
    payload = schedule_grid_payload(fx.store, MID, days=1)
    defense = team_defense(fx.store, MID)
    # every defense-data team is a row even when the 1-day window has no games
    assert set(defense.allowed) <= {t["team"] for t in payload["teams"]}


def test_empty_store_is_loud():
    with pytest.raises(ValueError):
        schedule_grid_payload(Store(":memory:"), MID)
