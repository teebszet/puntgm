"""Web payload tests — the puntgm.com decision tools' read-only layer.

The payload is a thin, honest serializer over ``Projector``: numbers must equal the
engine's, and the variance the digest hides must be present and consistent with the
mean (band = mean ± 0.6745σ).
"""

from __future__ import annotations

from fantasy_gm.config import Config
from fantasy_gm.engine.projection import Projector
from fantasy_gm.webtools import games_remaining, matchup_payload, teams_payload

_P75_Z = 0.6744897501960817


def test_matchup_payload_matches_engine(fx):
    config = Config()
    proj = Projector(config=config).project(fx.store, fx.league_id, "T00", fx.as_of)
    payload = matchup_payload(fx.store, config, fx.league_id, "T00", fx.as_of)
    assert payload is not None, "the fixture league has an active matchup mid-week"
    assert payload["opponent_id"] == proj.opponent_id
    assert payload["period_index"] == proj.period_index
    by_cat = {row["category"]: row for row in payload["categories"]}
    assert set(by_cat) == set(proj.categories)
    for cat, p in proj.categories.items():
        row = by_cat[cat]
        assert row["mine"] == _round_like(cat, p.mine_total)
        assert row["opp"] == _round_like(cat, p.opp_total)
        assert row["win_prob"] == round(p.win_prob, 2)
        assert row["label"] == p.label


def test_payload_publishes_variance_the_digest_hides(fx):
    """Every row carries σ for both sides and a band consistent with its mean."""
    payload = matchup_payload(fx.store, Config(), fx.league_id, "T00", fx.as_of)
    assert payload is not None
    for row in payload["categories"]:
        for side in ("mine", "opp"):
            std = row[f"{side}_std"]
            mean = row[side]
            assert std >= 0.0
            lo, hi = row[f"{side}_lo"], row[f"{side}_hi"]
            assert lo <= mean <= hi
            # band endpoints are mean ± 0.6745σ, before rounding
            assert abs((mean - lo) - _P75_Z * std) <= 0.11
            assert abs((hi - mean) - _P75_Z * std) <= 0.11


def test_matchup_payload_resolves_short_team_id(fx):
    """t.N-style short ids resolve like the digest does, not silently None."""
    config = Config()
    full = "T00"
    payload = matchup_payload(fx.store, config, fx.league_id, full, fx.as_of)
    assert payload is not None
    assert payload["team_id"] == full


def test_matchup_payload_no_active_matchup(build):
    """Outside any matchup window the payload is None, not an empty shell."""
    fx = build()
    payload = matchup_payload(fx.store, Config(), fx.league_id, "T00", "2025-07-01")
    assert payload is None


def test_games_remaining_none_without_matchup(fx):
    assert games_remaining(fx.store, fx.league_id, "T00", "2025-07-01") is None


def test_teams_payload_lists_every_team(fx):
    teams = teams_payload(fx.store, fx.league_id)
    assert len(teams) == 8
    assert {t["team_id"] for t in teams} == {f"T{i:02d}" for i in range(8)}
    assert all(t["name"] for t in teams)


def _round_like(cat: str, value: float) -> float:
    return round(value, 3 if cat in ("fg_pct", "ft_pct") else 1)
