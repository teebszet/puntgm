"""Tools API tests — the auth gate and the payload plumbing over a snapshot store.

The visitor grant is identity-only, so the handshake is exercised at the seam:
``yahoo_exchange`` / ``yahoo_guid`` are patched at module level, never networked.
Every denied door must deny (401 unsigned, 503 unconfigured), and a signed session
must reach the real payload code against a real (synthetic) store file.

TestClient runs with follow_redirects=False: the auth routes answer 303 to Yahoo
or /tools/, and the test reads the Set-Cookie jar from each hop directly — the
secure-flagged cookies would otherwise not round-trip over the plain-http
testserver.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from api import index as api
from fantasy_gm.data.simulate import simulate_league
from fantasy_gm.data.store import Store
from fantasy_gm.data.synthetic import seed_synthetic_season

SEASON = "2025-26"


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    """A synthetic-season store file + matching env; returns (client, league_id, path)."""
    path = tmp_path / "snapshot.sqlite"
    store = Store(path)
    seed_synthetic_season(store, season=SEASON, seed=7)
    league_id = simulate_league(store, season=SEASON, seed=1, n_teams=4, roster_size=5)
    # The synthetic logs run through the season's last game, so the derived as_of
    # (newest log + 1 day) lands after the final scheduled matchup week — add the
    # following week so the week-derivation contract has an active week to read.
    import datetime as dt

    from fantasy_gm.models import Matchup

    rows = store.conn.execute(
        "SELECT period_index, period_end FROM matchups WHERE league_id = ? "
        "ORDER BY period_index DESC LIMIT 1", (league_id,)).fetchall()
    idx, last_end = rows[0]["period_index"], rows[0]["period_end"]
    start = dt.date.fromisoformat(last_end) + dt.timedelta(days=1)
    store.add_matchup(Matchup(league_id, idx + 1, start.isoformat(),
                              (start + dt.timedelta(days=6)).isoformat(), "T00", "T01"))
    store.close()

    monkeypatch.setenv("FANTASY_GM_STORE", str(path))
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("TOOLS_LEAGUE_ID", league_id)
    for var in ("YAHOO_CLIENT_ID", "YAHOO_CLIENT_SECRET", "YAHOO_REDIRECT_URI",
                "YAHOO_ALLOWED_GUID"):
        monkeypatch.delenv(var, raising=False)
    return TestClient(api.app, follow_redirects=False), league_id, str(path)


def _login_env(monkeypatch, guid="GUID-TIM"):
    monkeypatch.setenv("YAHOO_CLIENT_ID", "cid")
    monkeypatch.setenv("YAHOO_CLIENT_SECRET", "sec")
    monkeypatch.setenv("YAHOO_REDIRECT_URI", "https://www.puntgm.com/api/auth/callback")
    monkeypatch.setenv("YAHOO_ALLOWED_GUID", guid)


def test_health_reports_snapshot_basis(snapshot):
    client, _league, path = snapshot
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["auth_ready"] is False
    assert body["league_id"] == "yahoo-478.l.25733" or body["league_id"]
    # as_of = newest banked log + 1 day (the morning-after convention), read from data.
    store = Store(path)
    newest = store.conn.execute("SELECT MAX(game_date) d FROM player_logs").fetchone()["d"]
    store.close()
    assert body["as_of"] > newest


def test_tools_deny_unsigned_and_unconfigured(snapshot, monkeypatch):
    client, _, _ = snapshot
    assert client.get("/api/tools/wire-lines").status_code == 401

    monkeypatch.delenv("SESSION_SECRET")  # deployment gap, not a login need → 503
    fresh = TestClient(api.app, follow_redirects=False)
    assert fresh.get("/api/tools/wire-lines").status_code == 503


def test_wire_lines_serves_the_payload(snapshot):
    client, _league, _path = snapshot
    client.cookies.set(api.SESSION_COOKIE, api.make_session("test-secret", "GUID-TIM"))
    r = client.get("/api/tools/wire-lines")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"as_of", "game_date", "week", "slate", "rows", "near_misses",
                         "rostered_count"}
    assert body["week"]["start"] <= body["as_of"] <= body["week"]["end"]
    assert body["slate"]["games"] >= 1


def test_login_redirect_carries_the_gate_inputs(snapshot, monkeypatch):
    client, _, _ = snapshot
    assert client.get("/api/auth/login").status_code == 503

    _login_env(monkeypatch)
    r = client.get("/api/auth/login")
    assert r.status_code == 303
    location = r.headers["location"]
    assert location.startswith(api.AUTH_URL)
    assert "client_id=cid" in location and "scope=openid" in location
    assert "state=" in location
    assert r.cookies.get(api.STATE_COOKIE)  # CSRF state rides back on the callback


def test_callback_rejects_a_state_mismatch(snapshot, monkeypatch):
    client, _, _ = snapshot
    _login_env(monkeypatch)
    r = client.get("/api/auth/callback", params={"code": "c", "state": "wrong"})
    assert r.status_code == 400


def test_callback_happy_path_mints_the_session(snapshot, monkeypatch):
    client, _, _ = snapshot
    _login_env(monkeypatch, guid="GUID-TIM")
    login = client.get("/api/auth/login")
    state = login.cookies[api.STATE_COOKIE]
    client.cookies.set(api.STATE_COOKIE, state)

    called = {}

    def fake_exchange(code, settings):
        called["code"] = code
        return {"access_token": "t", "xoauth_yahoo_guid": "GUID-TIM"}

    monkeypatch.setattr(api, "yahoo_exchange", fake_exchange)
    r = client.get("/api/auth/callback", params={"code": "one-time", "state": state})
    assert called["code"] == "one-time"
    assert r.status_code == 303 and r.headers["location"] == "/tools/"
    session = r.cookies.get(api.SESSION_COOKIE)
    assert session

    client.cookies.set(api.SESSION_COOKIE, session)
    me = client.get("/api/auth/me").json()
    assert me == {"signed_in": True, "guid": "GUID-TIM"}


def test_callback_refuses_a_foreign_account(snapshot, monkeypatch):
    client, _, _ = snapshot
    _login_env(monkeypatch, guid="GUID-TIM")
    state = client.get("/api/auth/login").cookies[api.STATE_COOKIE]
    client.cookies.set(api.STATE_COOKIE, state)
    monkeypatch.setattr(api, "yahoo_exchange",
                        lambda code, settings: {"xoauth_yahoo_guid": "GUID-OTHER"})
    r = client.get("/api/auth/callback", params={"code": "c", "state": state})
    assert r.status_code == 403
    assert not r.cookies.get(api.SESSION_COOKIE)


def test_callback_maps_a_handshake_failure_to_502(snapshot, monkeypatch):
    client, _, _ = snapshot
    _login_env(monkeypatch)
    state = client.get("/api/auth/login").cookies[api.STATE_COOKIE]
    client.cookies.set(api.STATE_COOKIE, state)

    def boom(code, settings):
        raise RuntimeError("yahoo down")

    monkeypatch.setattr(api, "yahoo_exchange", boom)
    assert client.get("/api/auth/callback", params={"code": "c", "state": state}).status_code == 502


def test_callback_falls_back_to_userinfo_for_the_guid(snapshot, monkeypatch):
    client, _, _ = snapshot
    _login_env(monkeypatch, guid="GUID-TIM")
    state = client.get("/api/auth/login").cookies[api.STATE_COOKIE]
    client.cookies.set(api.STATE_COOKIE, state)
    monkeypatch.setattr(api, "yahoo_exchange", lambda code, settings: {"access_token": "t"})

    def fake_guid(token, settings):
        assert token["access_token"] == "t"
        return "GUID-TIM"

    monkeypatch.setattr(api, "yahoo_guid", fake_guid)
    assert client.get("/api/auth/callback",
                      params={"code": "c", "state": state}).status_code == 303


def _id_token(sub: str | None) -> str:
    import base64 as b64
    import json as jsonlib

    def seg(obj: dict) -> str:
        return b64.urlsafe_b64encode(jsonlib.dumps(obj).encode()).decode().rstrip("=")

    payload = {} if sub is None else {"sub": sub}
    return f"{seg({'alg': 'HS256'})}.{seg(payload)}.sig"


def test_guid_reads_the_id_token_sub_without_userinfo(monkeypatch):
    token = {"access_token": "t", "id_token": _id_token("GUID-TIM")}

    def no_network(*a, **k):
        raise AssertionError("userinfo must not be called when id_token carries sub")

    monkeypatch.setattr("requests.get", no_network)
    assert api.yahoo_guid(token, object()) == "GUID-TIM"


def test_guid_prefers_a_guid_claim_over_sub(monkeypatch):
    import base64 as b64
    import json as jsonlib

    def seg(obj: dict) -> str:
        return b64.urlsafe_b64encode(jsonlib.dumps(obj).encode()).decode().rstrip("=")

    body = seg({"xoauth_yahoo_guid": "REAL-GUID", "sub": "APP-SCOPED"})
    token = {"access_token": "t", "id_token": f"hdr.{body}.sig"}
    monkeypatch.setattr("requests.get", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("userinfo must not be called")))
    assert api.yahoo_guid(token, object()) == "REAL-GUID"


def test_guid_id_token_without_sub_falls_through_to_userinfo(monkeypatch):
    token = {"access_token": "t", "id_token": _id_token(None)}
    assert api._guid_from_id_token({"id_token": "junk"}) is None

    def userinfo_reached(*a, **k):
        raise RuntimeError("userinfo reached")

    monkeypatch.setattr("requests.get", userinfo_reached)
    with pytest.raises(RuntimeError, match="userinfo reached"):
        api.yahoo_guid(token, object())


def test_session_cookie_resists_tamper_and_expiry():
    token = api.make_session("k", "g")
    assert api.read_session("k", token) == "g"
    assert api.read_session("k", token[:-1] + ("0" if token[-1] != "0" else "1")) is None
    assert api.read_session("other-key", token) is None
    assert api.read_session("k", "garbage") is None
    expired = api.make_session("k", "g", now=time.time() - 31 * 86400)
    assert api.read_session("k", expired) is None
