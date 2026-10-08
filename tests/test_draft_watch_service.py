"""Draft-watch service plumbing: arm-time state reset, trigger validation, watcher cleanup.

The 2026-10-08 bug this pins down: arming a new draft id left the previous draft's
picks on the page because (a) nothing reset the state file between drafts and (b)
launchd killed every nohup-spawned watcher before its first write (the runner exits
and, without AbandonProcessGroup/setsid, takes its process group with it). The arm
contract tested here: arm == kill watchers + fresh 'armed' state, always.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

SERVICE = Path(__file__).resolve().parents[1] / "scripts" / "draft_watch_service.py"


def _load_service():
    spec = importlib.util.spec_from_file_location("draft_watch_service", SERVICE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def svc():
    return _load_service()


def _old_state(tmp_path: Path) -> Path:
    """A state file that looks like the previous draft: 153 picks, stopped."""
    state = tmp_path / "serve_state.json"
    state.write_text(json.dumps({
        "version": 1, "league": "478.l.2654071", "seat": 1, "interval_s": 15.0,
        "status": "stopped", "pick_count": 153,
        "picks": [{"number": 1, "seat": 1, "player_id": "203999", "name": "Nikola Jokic"}],
        "issues": ["something"], "events": [{"at": "05:57:08", "text": "old"}],
        "recommendation": {"candidates": ["x"]}, "error": None,
        "watcher_pid": None, "heartbeat_at": "2026-10-08T05:57:08Z",
    }))
    return state


def _dummy_watcher() -> subprocess.Popen:
    """A process whose cmdline contains the watcher marker (but does nothing)."""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)",
         "draft_watch_service.py", "watch"])


def _wait_pgrep(pid: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        out = subprocess.run(["pgrep", "-f", "draft_watch_service.py watch"],
                             capture_output=True, text=True)
        if str(pid) in out.stdout.split():
            return
        time.sleep(0.1)
    pytest.fail(f"dummy watcher {pid} never became visible to pgrep")


# --- reset_state -------------------------------------------------------------------


def test_reset_state_clears_previous_draft(svc, tmp_path):
    state = _old_state(tmp_path)
    out = svc.reset_state(state, "478.l.99999", 3, 30)
    assert out["league"] == "478.l.99999"
    assert out["seat"] == 3 and out["interval_s"] == 30.0
    assert out["status"] == "armed"
    assert out["picks"] == [] and out["pick_count"] == 0
    assert out["issues"] == [] and out["recommendation"] is None
    assert out["error"] is None and out["watcher_pid"] is None
    assert "armed" in out["events"][-1]["text"]
    on_disk = json.loads(state.read_text())
    assert on_disk["picks"] == [] and on_disk["league"] == "478.l.99999"


def test_reset_state_records_scheduled_start(svc, tmp_path):
    state = tmp_path / "serve_state.json"
    out = svc.reset_state(state, "478.l.99999", 1, 15, start_utc="2026-10-09T01:30")
    assert out["start_utc"] == "2026-10-09T01:30"
    assert "2026-10-09T01:30" in out["events"][-1]["text"]


# --- handle_trigger ----------------------------------------------------------------


def test_handle_trigger_rejects_bad_params(svc, tmp_path):
    state = tmp_path / "serve_state.json"
    cases = [
        {"league": ""}, {"league": "478"},
        {"league": "478.l.99999", "seat": "13"},
        {"league": "478.l.99999", "interval": "3"},
        {"league": "478.l.99999", "start": "tomorrow"},
    ]
    for params in cases:
        code, payload = svc.handle_trigger(params, state)
        assert code == 400 and not payload["ok"], params
        assert not state.exists()  # nothing armed on a rejected arm


def test_handle_trigger_arms_and_resets(svc, tmp_path):
    state = _old_state(tmp_path)
    code, payload = svc.handle_trigger(
        {"league": "478.l.99999", "seat": "5", "interval": "15", "start": ""},
        state)
    assert code == 200 and payload["ok"]
    assert payload["killed_watchers"] == []
    trigger = json.loads((tmp_path / "trigger.json").read_text())
    assert trigger["league"] == "478.l.99999" and trigger["seat"] == 5
    on_disk = json.loads(state.read_text())
    assert on_disk["status"] == "armed"
    assert on_disk["picks"] == [] and on_disk["pick_count"] == 0
    assert on_disk["seat"] == 5


def test_handle_trigger_keeps_start_utc(svc, tmp_path):
    state = tmp_path / "serve_state.json"
    code, _ = svc.handle_trigger(
        {"league": "478.l.99999", "seat": "1", "interval": "15",
         "start": "2026-10-09T01:30"}, state)
    assert code == 200
    assert json.loads(state.read_text())["start_utc"] == "2026-10-09T01:30"


# --- kill_watchers -----------------------------------------------------------------


def test_kill_watchers_terminates_recorded_pid(svc, tmp_path):
    dummy = _dummy_watcher()
    try:
        _wait_pgrep(dummy.pid)
        state = tmp_path / "serve_state.json"
        state.write_text(json.dumps({"watcher_pid": dummy.pid}))
        killed = svc.kill_watchers(state, grace=1.0)
        assert dummy.pid in killed
        assert dummy.wait(timeout=5) is not None
    finally:
        if dummy.poll() is None:
            dummy.kill()
            dummy.wait(timeout=5)


def test_kill_watchers_finds_unrecorded_watcher_via_pgrep(svc, tmp_path):
    dummy = _dummy_watcher()
    try:
        _wait_pgrep(dummy.pid)
        state = tmp_path / "serve_state.json"
        state.write_text(json.dumps({"watcher_pid": None}))
        killed = svc.kill_watchers(state, grace=1.0)
        assert dummy.pid in killed
        assert dummy.wait(timeout=5) is not None
    finally:
        if dummy.poll() is None:
            dummy.kill()
            dummy.wait(timeout=5)


def test_kill_watchers_spares_lookalike_and_stale_pids(svc, tmp_path):
    lookalike = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        state = tmp_path / "serve_state.json"
        # lookalike has no marker in its cmdline and the stale pid never existed;
        # neither may be signalled, and neither may crash the sweep.
        state.write_text(json.dumps({"watcher_pid": 999_999_999}))
        killed = svc.kill_watchers(state, grace=0.2)
        assert killed == []
        assert lookalike.poll() is None  # still running
    finally:
        if lookalike.poll() is None:
            lookalike.kill()
            lookalike.wait(timeout=5)


def test_kill_watchers_tolerates_missing_or_garbled_state(svc, tmp_path):
    assert svc.kill_watchers(tmp_path / "does_not_exist.json", grace=0.1) == []
    garbled = tmp_path / "garbled.json"
    garbled.write_text("{not json")
    assert svc.kill_watchers(garbled, grace=0.1) == []
