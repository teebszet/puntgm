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
  page viewer can never execute anything).

Everything the watcher knows lands in the state file after every change: picks in
order, discrepancy lines (never silently reconciled -- see live.reconcile), the latest
recommendation as structured candidates, and a heartbeat. The page shows exactly what
the terminal watcher would print.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# --- shared state file helpers ---------------------------------------------------

STATE_VERSION = 1


def new_state(league: str, seat: int, interval: float, started_at: str) -> dict:
    return {
        "version": STATE_VERSION,
        "league": league,
        "seat": seat,
        "interval_s": interval,
        "status": "starting",
        "started_at": started_at,
        "updated_at": started_at,
        "board_players": None,
        "pick_count": 0,
        "picks": [],
        "issues": [],
        "events": [],
        "recommendation": None,
        "error": None,
        "heartbeat_at": started_at,
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


# --- watch mode -------------------------------------------------------------------

def run_watch(league: str, seat: int, interval: float, state_path: Path,
              serve_port: int | None, stop_file: Path) -> int:
    from fantasy_gm.config import Config
    from fantasy_gm.data.store import Store
    from fantasy_gm.draft.live import (
        DraftState, build_gm, poll_draft_results, recommend, reconcile,
    )

    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    state = new_state(league, seat, interval, started)
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
    draft_state = DraftState(league_key=league, n_teams=12, my_seat=seat)
    # Re-arm on the same league+seat resumes the recorded draft (crash recovery):
    # picks already in the state file are adopted so nothing is lost or re-logged.
    if (prev and prev.get("league") == league and prev.get("seat") == seat
            and prev.get("picks")):
        from fantasy_gm.draft.live import Pick
        draft_state.picks = [
            Pick(number=pk["number"], player_id=pk["player_id"],
                 team_seat=pk["seat"], source="live", name=pk.get("name", ""))
            for pk in prev["picks"]
        ]
        log_line(state, f"resumed {len(draft_state.picks)} picks from previous run")
    state["status"] = "watching"
    state["board_players"] = len(pool)
    log_line(state, f"board ready: {len(pool)} players; watching seat {seat} of {league}")
    write_state(state_path, state)

    seen = 0
    while True:
        if stop_file.exists():
            log_line(state, "stop requested; watcher exits (page goes stale on purpose)")
            state["status"] = "stopped"
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

        issues = reconcile(draft_state, picks, names)
        for i in issues:
            log_line(state, f"! {i}")
            print(f"  ! {i}", flush=True)
        state["issues"] = issues
        if len(draft_state.picks) > seen:
            for p in draft_state.picks[seen:]:
                who = "YOU" if p.team_seat == seat else f"seat {p.team_seat}"
                nm = p.name or names.get(p.player_id, p.player_id)
                log_line(state, f"#{p.number} {who}: {nm}")
                print(f"#{p.number:>3} {who:<4} {nm}", flush=True)
            seen = len(draft_state.picks)
        state["pick_count"] = len(draft_state.picks)
        state["picks"] = [
            {"number": p.number, "seat": p.team_seat, "player_id": p.player_id,
             "name": p.name or names.get(p.player_id, p.player_id)}
            for p in draft_state.picks
        ]
        if draft_state.is_my_pick():
            rec = recommend(store, "2025-26", draft_state, pool, board=gm["board"],
                            adp_order=gm["adp_order"], names=names, budget_s=8.0)
            rendered = "\n".join(
                f"  {c.name}  value {c.total:.2f}  vs safe {c.value_over_safe:+.2f}  "
                f"surv {c.survival:.0%}" for c in rec.candidates
            ) or "  no candidates"
            print(f"Pick {rec.pick_number} -- seat {rec.on_the_clock} on the clock\n{rendered}",
                  flush=True)
            state["recommendation"] = {
                "pick_number": rec.pick_number,
                "on_the_clock": rec.on_the_clock,
                "my_seat": rec.my_seat,
                "mode": rec.mode,
                "degraded": rec.degraded,
                "note": rec.note,
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
                """Write trigger.json for the runner launchd job to consume.

                The browser never touches the filesystem; it POSTs the form values and
                this endpoint validates and persists them. The runner (fired every 60s)
                picks the file up, waits for the start time and starts the watcher.
                """
                from urllib.parse import parse_qs, urlparse

                q = parse_qs(urlparse(self.path).query)
                get = lambda k: (q.get(k) or [""])[0]  # noqa: E731 - tiny local helper
                league = get("league").strip()
                seat = get("seat") or "1"
                interval = get("interval") or "15"
                start = get("start").strip()
                if not league or not __import__("re").fullmatch(r"\d+\.l\.\d+", league):
                    self._reply(400, {"ok": False,
                                      "error": "league must look like 26357.l.12345"})
                    return
                if not seat.isdigit() or not 1 <= int(seat) <= 12:
                    self._reply(400, {"ok": False, "error": "seat must be 1-12"})
                    return
                if not interval.isdigit() or not 5 <= int(interval) <= 120:
                    self._reply(400, {"ok": False, "error": "interval must be 5-120 seconds"})
                    return
                if start and not __import__("re").fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", start[:16]):
                    self._reply(400, {"ok": False,
                                      "error": "start must be UTC YYYY-MM-DDTHH:MM"})
                    return
                trigger_path = state_file.parent / "trigger.json"
                tmp = Path(str(trigger_path) + ".tmp")
                tmp.write_text(json.dumps({
                    "league": league, "seat": int(seat), "interval": int(interval),
                    "start_utc": start[:16],
                    "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }, indent=1))
                tmp.replace(trigger_path)
                self._reply(200, {"ok": True, "trigger": str(trigger_path)})

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
    print(USAGE)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
