"""Draft-watch web service: one page to start the watcher, live recs on the same page.

Why: Tim asked (fantasy-nba-gm, 2026-10-06) for a page he can submit the mock-draft
inputs on -- and for the page itself to show the live recommendations as the watcher
makes them. The page is served through Tailscale Funnel (token path, same pattern as
the evidence pages); submission arms a launchd runner that starts this service in
watcher mode; the page then polls /state.json and renders picks, discrepancies and
on-the-clock recommendations.

One process, two modes:

* ``... serve <dir> <port> <state.json>`` -- static file server (Range support) plus
  GET /state.json read from the watcher's state file; this is the page.

* ``... watch <league> --seat N --interval S --out <state.json> [--serve-port P]``
  -- the watcher loop, plus a localhost HTTP server answering /state.json from memory
  (dev/testing convenience only: binds 127.0.0.1; the Funnel proxy reaches the page
  through the separate ``serve`` process reading the same state file, so the public
  page viewer can never execute anything). The watcher detaches into its own session
  (setsid) at startup and records its pid in the state file, so re-arming can find and
  replace it precisely.

* ``... reset-state --out <state.json> --league <key> [--seat N] [--interval S]
  [--start UTC]`` -- runner helper: overwrite the state file with a fresh 'armed'
  state (no picks, no recs), killing nothing.

Arm lifecycle (2026-10-08 redesign): clicking Arm makes the serve process kill every
watcher and reset the state file immediately, then leave trigger.json for the launchd
runner, which kills any survivors and resets the state again before spawning the new
watcher. Exactly one watcher exists at any time, and the page never shows a previous
draft's picks after an arm.

Everything the watcher knows lands in the state file after every change: picks in
order, discrepancy lines (never silently reconciled -- see live.reconcile), the latest
recommendation as structured candidates, and a heartbeat. The page shows exactly what
the terminal watcher would print.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# --- shared state file helpers ---------------------------------------------------

STATE_VERSION = 1

# pgrep/ps marker identifying watcher processes (not serve, not the page).
WATCHER_MARKER = "draft_watch_service.py watch"


def new_state(league: str, seat: int, interval: float, started_at: str,
              start_utc: str | None = None, status: str = "starting") -> dict:
    return {
        "version": STATE_VERSION,
        "league": league,
        "seat": seat,
        "interval_s": interval,
        "status": status,
        "started_at": started_at,
        "updated_at": started_at,
        "start_utc": start_utc,
        "board_players": None,
        "pick_count": 0,
        "picks": [],
        "issues": [],
        "events": [],
        "recommendation": None,
        "error": None,
        "heartbeat_at": started_at,
        "watcher_pid": None,
    }


def load_state(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


def write_state(path: str | Path, state: dict) -> None:
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(path)


def log_line(state: dict, text: str) -> None:
    """Append an event line; the page renders these in order."""
    state["events"].append(
        {"at": time.strftime("%H:%M:%S", time.gmtime()), "text": text}
    )
    del state["events"][:-200]


# --- arm-time helpers: fresh state + watcher cleanup -------------------------------

def reset_state(state_file: str | Path, league: str, seat: int, interval: float,
                start_utc: str | None = None) -> dict:
    """Overwrite the state file with a fresh 'armed' state for a new draft.

    Arming must clear the previous draft's picks immediately (2026-10-08: the page
    kept showing the last draft's 153 picks until the new watcher's first write --
    which, before the setsid fix, never came). Call this the moment an arm is
    accepted, before the watcher starts.
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state = new_state(league, seat, interval, now, start_utc=start_utc, status="armed")
    when = f", starts {start_utc} UTC" if start_utc else ", starting within a minute"
    log_line(state, f"armed: league {league} seat {seat}{when} — previous draft cleared")
    write_state(state_file, state)
    return state


def _pid_cmdline(pid: int) -> str:
    try:
        out = subprocess.run(["ps", "-ww", "-p", str(pid), "-o", "command="],
                             capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_watchers(state_file: str | Path, grace: float = 2.0) -> list[int]:
    """Terminate every watcher process; return the pids that were signalled.

    Candidates come from two places so neither can be fooled alone: the recorded
    ``watcher_pid`` in the state file (exact process this page watched) and a
    pgrep sweep (covers watchers started before this field existed or by hand).
    Every candidate is verified by cmdline before signalling, so a recycled pid
    can never be killed, and the serve process itself is never a candidate (its
    cmdline says ``serve``, not ``watch``).
    """
    candidates: set[int] = set()
    try:
        prev = json.loads(Path(state_file).read_text())
        recorded = prev.get("watcher_pid")
        if isinstance(recorded, int) and recorded > 1:
            candidates.add(recorded)
    except (OSError, ValueError):
        pass
    try:
        out = subprocess.run(["pgrep", "-f", WATCHER_MARKER],
                             capture_output=True, text=True, timeout=5)
        candidates.update(int(p) for p in out.stdout.split())
    except (OSError, ValueError, subprocess.SubprocessError):
        pass

    me = os.getpid()
    killed: list[int] = []
    for pid in sorted(candidates):
        if pid <= 1 or pid == me:
            continue
        if WATCHER_MARKER not in _pid_cmdline(pid):
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + grace
    while killed and time.monotonic() < deadline:
        if not any(_pid_alive(p) for p in killed):
            break
        time.sleep(0.1)
    for pid in killed:  # grace over: survivors get SIGKILL
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    return killed


def handle_trigger(params: dict, state_file: str | Path) -> tuple[int, dict]:
    """Arm a draft: validate, kill any running watcher, write trigger.json and
    reset the state file to a fresh 'armed' state -- all before the reply, so the
    page switches to the new draft the moment Arm is clicked (the runner only
    consumes the trigger on its next tick, up to 60s later).

    ``params`` maps form names to single strings; returns (http_code, payload).
    """
    state_file = Path(state_file)
    league = str(params.get("league") or "").strip()
    seat = str(params.get("seat") or "1")
    interval = str(params.get("interval") or "15")
    start = str(params.get("start") or "").strip()
    if not league or not re.fullmatch(r"\d+\.l\.\d+", league):
        return 400, {"ok": False,
                     "error": "league must look like 478.l.2638432 (the page adds the 478.l. prefix itself)"}
    if not seat.isdigit() or not 1 <= int(seat) <= 12:
        return 400, {"ok": False, "error": "seat must be 1-12"}
    if not interval.isdigit() or not 5 <= int(interval) <= 120:
        return 400, {"ok": False, "error": "interval must be 5-120 seconds"}
    if start and not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", start[:16]):
        return 400, {"ok": False, "error": "start must be UTC YYYY-MM-DDTHH:MM"}

    killed = kill_watchers(state_file)
    trigger_path = state_file.parent / "trigger.json"
    tmp = Path(str(trigger_path) + ".tmp")
    tmp.write_text(json.dumps({
        "league": league, "seat": int(seat), "interval": int(interval),
        "start_utc": start[:16],
        "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=1))
    tmp.replace(trigger_path)
    reset_state(state_file, league, int(seat), int(interval),
                start_utc=start[:16] if start else None)
    return 200, {"ok": True, "trigger": str(trigger_path), "killed_watchers": killed}


# --- watch mode -------------------------------------------------------------------

def run_watch(league: str, seat: int, interval: float, state_path: Path,
              serve_port: int | None, stop_file: Path) -> int:
    # Detach from the spawner's process group FIRST: launchd kills a job's whole
    # process group when the job exits (no AbandonProcessGroup), which silently
    # killed every nohup-spawned watcher before its first write (2026-10-08 --
    # arming a new draft then showed the previous draft forever). setsid moves us
    # to our own session, immune to that kill no matter who spawned us.
    try:
        os.setsid()
    except OSError:
        pass  # already a session leader (e.g. run interactively); nothing to detach
    from fantasy_gm.config import Config
    from fantasy_gm.data.store import Store
    from fantasy_gm.draft.live import (
        DraftState, build_gm, poll_draft_results, recommend, reconcile,
    )

    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state = new_state(league, seat, interval, started)
    state["watcher_pid"] = os.getpid()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    # Snapshot the previous run's state before this run's first write overwrites it,
    # so the resume below sees the recorded draft rather than the fresh empty state.
    try:
        prev = load_state(state_path)
    except FileNotFoundError:
        prev = None
    write_state(state_path, state)

    def http_state_server() -> None:
        """Answer the page's state.json straight from memory (localhost only)."""

        class H(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                if self.path.split("?")[0] == "/state.json":
                    body = json.dumps(state, indent=1).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_error(404)

            def log_message(self, *a) -> None:  # silence per-request stderr
                pass

        ThreadingHTTPServer(("127.0.0.1", serve_port), H).serve_forever()

    if serve_port:
        threading.Thread(target=http_state_server, daemon=True).start()

    print("Building board + market order (once) ...", flush=True)
    store = Store(Config().db_path)
    gm = build_gm(store, "2025-26", "2025-10-20")
    pool = gm["pool"]
    names = gm["names"]
    # Full-universe directory, not the board-only names map: platform picks arrive with
    # Yahoo game ids and foreign-id names must resolve through the whole directory.
    dir_by_id, dir_by_key = gm["directory"]
    draft_state = DraftState(league_key=league, n_teams=12, my_seat=seat)
    # Re-arm on the same league+seat resumes the recorded draft (crash recovery):
    # picks already in the state file are adopted so nothing is lost or re-logged.
    if (prev and prev.get("league") == league and prev.get("seat") == seat
            and prev.get("picks")):
        from fantasy_gm.draft.live import Pick
        # Adopt only picks already in our id space; raw platform ids from an older run
        # (before the id translation existed) are re-fed and mapped by the platform
        # poll, so dropping them here loses nothing.
        adopted = [pk for pk in prev["picks"] if pk["player_id"] in dir_by_id]
        dropped = len(prev["picks"]) - len(adopted)
        draft_state.picks = [
            Pick(number=pk["number"], player_id=pk["player_id"],
                 team_seat=pk["seat"], source="live", name=pk.get("name", ""))
            for pk in adopted
        ]
        log_line(state, f"resumed {len(draft_state.picks)} picks from previous run"
                 + (f"; dropped {dropped} untranslatable platform ids (platform re-feeds them)"
                    if dropped else ""))
    state["status"] = "watching"
    state["board_players"] = len(pool)
    log_line(state, f"board ready: {len(pool)} players; watching seat {seat} of {league}")
    write_state(state_path, state)

    seen = 0
    placeholder_logged = False
    last_rec_printed_picks = -1
    while True:
        if stop_file.exists():
            log_line(state, "stop requested; watcher exits (page goes stale on purpose)")
            state["status"] = "stopped"
            state["watcher_pid"] = None
            write_state(state_path, state)
            stop_file.unlink()
            return 0
        state["heartbeat_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        try:
            picks = poll_draft_results(league)
            state["error"] = None
        except Exception as exc:  # noqa: BLE001 - the page must show every failure
            msg = f"poll failed: {exc}"
            print(f"[{msg}] -- manual entry remains available", flush=True)
            state["error"] = msg
            log_line(state, msg + " (manual entry remains available)")
            write_state(state_path, state)
            time.sleep(interval)
            continue

        n_placeholders = sum(1 for p in picks if p.get("player_id") is None)
        if n_placeholders and not placeholder_logged:
            placeholder_logged = True
            log_line(state, f"platform pre-fills {n_placeholders} empty draft-order slots; "
                            f"ignored (not errors)")
        issues = reconcile(draft_state, picks, dir_by_id, dir_by_key)
        for i in issues:
            log_line(state, f"! {i}")
            print(f"  ! {i}", flush=True)
        state["issues"] = issues
        if len(draft_state.picks) > seen:
            for p in draft_state.picks[seen:]:
                who = "YOU" if p.team_seat == seat else f"seat {p.team_seat}"
                nm = p.name or dir_by_id.get(p.player_id) or names.get(p.player_id, p.player_id)
                log_line(state, f"#{p.number} {who}: {nm}")
                print(f"#{p.number:>3} {who:<4} {nm}", flush=True)
            seen = len(draft_state.picks)
        state["pick_count"] = len(draft_state.picks)
        state["picks"] = [
            {"number": p.number, "seat": p.team_seat, "player_id": p.player_id,
             "name": p.name or dir_by_id.get(p.player_id) or names.get(p.player_id, p.player_id)}
            for p in draft_state.picks
        ]
        # Recs run every poll, not only on our turn: between our picks the card is a
        # preview -- top of the board with survival projected to OUR next pick (the
        # survival math was always relative to my_seat; the card now says so).
        my_next = draft_state.my_next_pick()
        if my_next is not None:
            rec = recommend(store, "2025-26", draft_state, pool, board=gm["board"],
                            adp_order=gm["adp_order"], names=names, budget_s=8.0)
            # Print the full rec block only when it changed meaningfully: our turn, or a
            # new pick landed since the last print (a per-poll print is near-duplicate
            # noise across 30-90s clocks).
            if draft_state.is_my_pick() or state["pick_count"] != last_rec_printed_picks:
                rendered = "\n".join(
                    f"  {c.name}  value {c.total:.2f}  vs safe {c.value_over_safe:+.2f}  "
                    f"surv {c.survival:.0%}" for c in rec.candidates
                ) or "  no candidates"
                print(f"Pick {rec.pick_number} -- seat {rec.on_the_clock} on the clock\n{rendered}",
                      flush=True)
                last_rec_printed_picks = state["pick_count"]
            state["recommendation"] = {
                "pick_number": rec.pick_number,
                "on_the_clock": rec.on_the_clock,
                "my_seat": rec.my_seat,
                "mode": rec.mode,
                "degraded": rec.degraded,
                "note": rec.note,
                "for_me": draft_state.is_my_pick(),
                "my_next_pick": my_next,
                "candidates": [
                    {
                        "name": c.name,
                        "board_rank": c.board_rank,
                        "total": round(c.total, 2),
                        "value_over_safe": round(c.value_over_safe, 2),
                        "survival": round(c.survival, 3),
                        "categories": {k.split("_")[0]: round(v, 2)
                                       for k, v in c.categories.items()},
                        "engine_value": None if c.engine_value is None else round(c.engine_value, 3),
                    }
                    for c in rec.candidates
                ],
            }
        else:
            state["recommendation"] = None
        write_state(state_path, state)
        time.sleep(interval)


# --- serve mode -------------------------------------------------------------------

class StateAwareStaticServer:
    """Static file server (Range support) plus /state.json read from disk.

    This is the process the Funnel proxy points at. /state.json reads the watcher's
    state file, so the page keeps working even if this process starts before or after
    the watcher.
    """

    def __init__(self, directory: Path, port: int, state_path: Path) -> None:
        directory = directory.resolve()
        state_file = Path(state_path)

        class H(SimpleHTTPRequestHandler):
            def __init__(self, *a, **kw):
                super().__init__(*a, directory=str(directory), **kw)

            def end_headers(self) -> None:  # noqa: N802 - stdlib naming
                # Everything this serves is a live document (page edits, watcher state).
                # Without this, browsers heuristically cache the HTML and keep showing a
                # stale page after an edit -- bit the form validation once (2026-10-07).
                self.send_header("Cache-Control", "no-store")
                super().end_headers()

            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                if self.path.split("?")[0] == "/state.json":
                    body = self._state_body()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    super().do_GET()

            def _state_body(self) -> bytes:
                try:
                    return json.dumps(load_state(state_file), indent=1).encode()
                except FileNotFoundError:
                    return json.dumps({"status": "waiting"}).encode()

            def _reply(self, code: int, payload: dict) -> None:
                body = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _do_trigger(self) -> None:
                """Arm via handle_trigger: kill running watchers, write trigger.json
                for the runner launchd job, and reset the state file so the page
                shows the NEW draft immediately (the runner only consumes the
                trigger on its next tick, up to 60s later)."""
                from urllib.parse import parse_qs, urlparse

                q = parse_qs(urlparse(self.path).query)
                params = {k: (v or [""])[0] for k, v in q.items()}
                code, payload = handle_trigger(params, state_file)
                self._reply(code, payload)

            def do_POST(self) -> None:  # noqa: N802 - stdlib naming
                """Only /trigger (arm the runner) and /stop (sentinel file). Nothing else."""
                route = self.path.split("?")[0]
                if route == "/trigger":
                    self._do_trigger()
                    return
                if route != "/stop":
                    self.send_error(404)
                    return
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = 0
                body = self.rfile.read(min(n, 4096)) if n else b""
                try:
                    ok = json.loads(body or b"{}").get("action") == "stop"
                except Exception:
                    ok = False
                if not ok:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"ok":false,"error":"expected {\"action\":\"stop\"}"}')
                    return
                Path(str(state_file) + ".stop").write_text("")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *a) -> None:  # silence per-request stderr
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", port), H)

    def serve_forever(self) -> None:
        self._httpd.serve_forever()


def run_serve(directory: str, port: int, state_path: str) -> int:
    StateAwareStaticServer(Path(directory), port, Path(state_path)).serve_forever()
    return 0


# --- cli --------------------------------------------------------------------------

USAGE = __doc__


def _flag(rest: list[str], name: str) -> str | None:
    return rest[rest.index(name) + 1] if name in rest else None


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(USAGE)
        return 1
    mode, rest = argv[0], argv[1:]
    if mode == "serve" and len(rest) >= 3:
        # Positional (dir port state) or named (--dir --port --state) both accepted;
        # the launchd job uses named flags so the plist reads at a glance.
        directory = _flag(rest, "--dir") or (rest[0] if not rest[0].startswith("--") else None)
        port = int(_flag(rest, "--port") or (rest[1] if len(rest) > 1 and not rest[1].startswith("--") else 0))
        state = _flag(rest, "--state") or (rest[2] if len(rest) > 2 and not rest[2].startswith("--") else None)
        if not (directory and port and state):
            print(USAGE)
            return 1
        run_serve(directory, port, state)
        return 0
    if mode == "watch" and rest:
        league = rest[0]
        seat = int(rest[rest.index("--seat") + 1]) if "--seat" in rest else 1
        interval = (float(rest[rest.index("--interval") + 1])
                    if "--interval" in rest else 15.0)
        state_path = (Path(rest[rest.index("--out") + 1]) if "--out" in rest
                      else Path("data/draft_watch_state.json"))
        serve_port = (int(rest[rest.index("--serve-port") + 1])
                      if "--serve-port" in rest else None)
        stop_file = (Path(rest[rest.index("--stop-file") + 1])
                     if "--stop-file" in rest else Path(str(state_path) + ".stop"))
        return run_watch(league, seat, interval, state_path, serve_port, stop_file)
    if mode == "reset-state" and "--out" in rest and "--league" in rest:
        # Runner helper: write a fresh 'armed' state at trigger-consume time so the
        # page never shows a previous draft, even when the trigger didn't come
        # through the page's own /trigger endpoint.
        out = Path(_flag(rest, "--out") or "")
        league = _flag(rest, "--league") or ""
        seat = int(_flag(rest, "--seat") or 1)
        interval = float(_flag(rest, "--interval") or 15)
        start = _flag(rest, "--start")
        reset_state(out, league, seat, interval, start_utc=start or None)
        return 0
    print(USAGE)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
