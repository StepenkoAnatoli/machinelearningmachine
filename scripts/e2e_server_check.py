"""End-to-end check against a *running* dashboard, over real HTTP and a real socket.

Nothing here uses TestClient or an in-process ASGI transport, so it exercises what a
browser actually touches: uvicorn's event loop, the cookie, the WebSocket handshake,
and the frame the feed writes while a run is in flight. Two tabs, because per-browser
isolation and "one run at a time" are the two promises that only show up when more
than one caller is involved.

    python scripts/e2e_server_check.py --base http://127.0.0.1:8799

Exit code 0 means every check passed. It assumes a server is already up on --base
(that is how CI uses it, right after installing and serving the built wheel).
"""

import argparse
import asyncio
import http.cookies
import json
import urllib.error
import urllib.request

import websockets

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--base", default="http://127.0.0.1:8000", help="URL of a running dashboard")
args = parser.parse_args()

BASE = args.base.rstrip("/")
WS = ("ws://" + BASE.split("://", 1)[1]) + "/ws"


def request(method, path, body=None, cookie=None):
    data = json.dumps(body).encode() if body is not None else None
    # S310: the scheme here is http:// to an address the operator passed as --base,
    # in a development harness. There is no user-controlled URL in this script.
    req = urllib.request.Request(BASE + path, data=data, method=method)  # noqa: S310
    req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            raw = resp.read().decode()
            jar = resp.headers.get("Set-Cookie")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        jar = exc.headers.get("Set-Cookie")
        return exc.code, json.loads(raw) if raw.strip().startswith(("{", "[")) else raw, jar
    return resp.status if hasattr(resp, "status") else 200, (json.loads(raw) if raw.strip().startswith(("{", "[")) else raw), jar


def session_cookie(set_cookie_header):
    if not set_cookie_header:
        return None
    morsel = http.cookies.SimpleCookie(set_cookie_header).get("mmm_session")
    return f"mmm_session={morsel.value}" if morsel else None


async def collect_until(ws, kinds, limit=400, timeout=30):
    frames = []
    deadline = asyncio.get_event_loop().time() + timeout
    while len(frames) < limit:
        left = deadline - asyncio.get_event_loop().time()
        if left <= 0:
            break
        try:
            frames.append(json.loads(await asyncio.wait_for(ws.recv(), left)))
        except asyncio.TimeoutError:
            break
        if any(f.get("type") in kinds for f in frames):
            break
    return frames


async def main():
    failures = []

    def check(name, ok, detail=""):
        print(("  PASS " if ok else "  FAIL ") + name + (f"  {detail}" if detail else ""))
        if not ok:
            failures.append(name)

    print("\n== tab A: status, run, frames ==")
    status, body, jar = request("GET", "/api/status")
    check("GET /api/status answers 200", status == 200, f"status={status}")
    cookie_a = session_cookie(jar)
    check("a session cookie was issued", bool(cookie_a), (cookie_a or "")[:24] + "...")
    check("history starts empty", body["messages"] == 0, f"messages={body['messages']}")
    check("the registry is observable", {"connections", "dropped_frames", "live_sessions", "idle_ttl_seconds"} <= set(body),
          f"live_sessions={body['live_sessions']} idle_ttl={body['idle_ttl_seconds']}")

    status, cfg, _ = request("GET", "/api/config")
    check("mode is simulated without keys", status == 200 and cfg["mode"] == "simulated", f"mode={cfg.get('mode')}")

    async with websockets.connect(WS, additional_headers={"Cookie": cookie_a}) as ws:
        hello = json.loads(await ws.recv())
        check("the first frame is the session snapshot", hello["type"] == "init", hello["type"])
        check("the snapshot is scoped to this session", len(hello["history"]) == 0 and len(hello["agents"]) == 4,
              f"history={len(hello['history'])} agents={len(hello['agents'])}")
        check("the client is told the run ceiling it will be held to",
              hello["flags"]["run_timeout_seconds"] == 180.0, str(hello.get("flags")))

        status, run, _ = request("POST", "/api/run", {"topology": "pipeline", "turns": 2, "prompt": "Design a rate limiter"}, cookie_a)
        check("run completes", status == 200 and run["status"] == "completed", f"status={status}")
        run_id = run["run_id"]
        check("the response names the run", bool(run_id))

        frames = await collect_until(ws, {"run_completed"}, timeout=10)
        types = [f["type"] for f in frames]
        check("the tab was pushed the transcript", types.count("new_message") >= 4, f"types={types}")
        check("run_started and run_completed arrived over the socket",
              "run_started" in types and "run_completed" in types, str(types))
        check("every pushed frame is stamped with the run id",
              all(f.get("run_id") == run_id for f in frames if f["type"] in ("run_started", "new_message", "run_completed")),
              str({f["type"]: f.get("run_id") for f in frames if "run_id" in f}))
        check("no gap notice on a healthy connection", "stream_gap" not in types)

        status, hist, _ = request("GET", "/api/history", None, cookie_a)
        check("history matches what was streamed", status == 200 and len(hist) >= 4, f"{len(hist)} messages")
        _, stat2, _ = request("GET", "/api/status", None, cookie_a)
        check("no frame was dropped by the server", stat2["dropped_frames"] == 0, str(stat2.get("dropped_frames")))

        print("\n== a second tab cannot see this tab's transcript ==")
        status, _, jar_b = request("GET", "/api/status")
        cookie_b = session_cookie(jar_b)
        check("a fresh browser gets its own cookie", cookie_b and cookie_b != cookie_a)
        status, hist_b, _ = request("GET", "/api/history", None, cookie_b)
        check("tab B sees an empty history", status == 200 and len(hist_b) == 0, f"{len(hist_b)} messages")
        _, stat_mid, _ = request("GET", "/api/status", None, cookie_a)
        check("the registry counts both live sessions", stat_mid["live_sessions"] >= 2, str(stat_mid["live_sessions"]))

        print("\n== concurrency in the same tab ==")
        # The run cadence gate is one second, so wait it out first: otherwise the
        # request that loses the race is refused for the wrong reason (429, not 409).
        await asyncio.sleep(1.2)
        tasks = await asyncio.gather(
            asyncio.to_thread(request, "POST", "/api/run",
                              {"topology": "p2p", "turns": 2, "prompt": "alpha-run-token please"}, cookie_a),
            asyncio.to_thread(request, "POST", "/api/run",
                              {"topology": "p2p", "turns": 2, "prompt": "bravo-run-token please"}, cookie_a),
        )
        codes = sorted(t[0] for t in tasks)
        check("one run was accepted, the other refused with 409", codes == [200, 409], f"codes={codes}")
        refused = next(t for t in tasks if t[0] == 409)
        check("the refusal is actionable", "already in progress" in json.dumps(refused[1]), str(refused[1])[:120])
        frames = await collect_until(ws, {"run_completed"}, timeout=10)
        check("the accepted run still completed on the socket", any(f["type"] == "run_completed" for f in frames))
        status, hist, _ = request("GET", "/api/history", None, cookie_a)
        ids = [m["content"] for m in hist]
        transcript = " ".join(ids)
        saw_alpha = "alpha-run-token" in transcript
        saw_bravo = "bravo-run-token" in transcript
        check("only one of the two runs reached the shared transcript",
              saw_alpha != saw_bravo, f"{len(ids)} messages, alpha={saw_alpha} bravo={saw_bravo}")

        print("\n== the documented prompt limit is the real limit ==")
        await asyncio.sleep(1.2)
        status, run, _ = request("POST", "/api/run", {"topology": "p2p", "turns": 1,
                                                       "prompt": "summarise this: " + "x" * 4980}, cookie_a)
        check("a 5000-character prompt runs", status == 200, f"status={status}")
        check("and reports what it had to shorten",
              isinstance(run.get("truncated_messages"), int), f"truncated_messages={run.get('truncated_messages')}")
        await asyncio.sleep(1.2)
        status, over, _ = request("POST", "/api/run", {"topology": "p2p", "turns": 1,
                                                        "prompt": "x" * 5001}, cookie_a)
        detail = json.dumps(over)
        check("one character more is refused up front, not truncated in silence",
              status == 422 and "5000" in detail, f"status={status} detail={detail[:120]}")

        print("\n== saved transcripts are per-tab ==")
        await asyncio.sleep(1.2)
        status, saved, _ = request("POST", "/api/sessions", {"name": "e2e smoke"}, cookie_a)
        check("the run can be saved", status == 200, f"status={status} {json.dumps(saved)[:120]}")
        if status == 200:
            listed_b = request("GET", "/api/sessions", None, cookie_b)[1]
            listed_a = request("GET", "/api/sessions", None, cookie_a)[1]
            names_a = [s["name"] for s in listed_a["sessions"]]
            names_b = [s["name"] for s in listed_b["sessions"]]
            check("tab A sees its saved session", "e2e smoke" in names_a, str(names_a))
            check("tab B does not", "e2e smoke" not in names_b, str(names_b))
            row = next(s for s in listed_a["sessions"] if s["name"] == "e2e smoke")
            check("the list row states how long the transcript is",
                  isinstance(row.get("message_count"), int) and row["message_count"] >= 4, str(row))
            back = request("GET", f"/api/sessions/{row['id']}", None, cookie_a)[1]
            check("and the saved transcript loads back with its count",
                  back.get("message_count") == row["message_count"], f"stored={len(back.get('messages', []))}")
            request("DELETE", f"/api/sessions/{row['id']}", None, cookie_a)
            after = request("GET", "/api/sessions", None, cookie_a)[1]
            check("deleting removes it for that tab only",
                  "e2e smoke" not in [s["name"] for s in after["sessions"]])

    print("\n== health and shutdown ==")
    status, health, _ = request("GET", "/health")
    check("health answers", status == 200 and health["status"] == "ok", str(health))

    print()
    if failures:
        print(f"FAILURES ({len(failures)}): " + ", ".join(failures))
        return 1
    print(f"ALL E2E CHECKS PASSED ({BASE})")
    return 0


raise SystemExit(asyncio.run(main()))
