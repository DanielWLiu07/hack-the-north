#!/usr/bin/env python3
"""verify_robot_link.py — prove the robot -> laptop data link, end to end, or say exactly why not.

    python scripts/verify_robot_link.py                     # PI_HOST:PI_PORT from .env
    python scripts/verify_robot_link.py --host 100.x.y.z    # a specific address
    python scripts/verify_robot_link.py --soak 120          # hold the stream for two minutes
    python scripts/verify_robot_link.py --capture           # also pull one capture (moves real MB)
    python scripts/verify_robot_link.py --json              # the record, for PROGRESS.md

What it proves, in the order a link fails:

    path      the packets get there, and HOW: direct WireGuard, relayed via DERP, or plain LAN
    healthz   robot/server.py answers, and which robot it is (hardware | sim | replay)
    sse       GET /events: framing, `hello` first with the clock pairing, ids <boot>:<n> contiguous
    resume    reconnect with Last-Event-ID n -> the next event is n+1. No gap, no duplicate
    restart   a Last-Event-ID from ANOTHER boot is answered with `gap boot_changed`, never silently
    idle      a quiet (filtered) stream still carries `:` keepalives — a dead link is detectable
    soak      the stream held for --soak seconds: no disconnect, no stall, every id accounted for
    ws        /stream, the WebSocket telemetry/hub.py actually consumes (docs/16 §3)
    capture   (--capture) one POST /capture, inline — the 5 MB path, and the link's real throughput

The contract is docs/16 §3c (robot/server.py's /events). Standard library only, plus `websockets`
for the ws stage if it is installed.

THE VERDICT IS ABOUT THE LINK, NOT THE ENDPOINT. A run against this laptop's own address proves the
server and this script and says so — no packet left the machine. Exit 0 only for a remote robot:

    0  LINK VERIFIED              every stage passed, against another machine
    2  checks passed, NOT a link  the target is this laptop (or a stand-in). Proves nothing about reach
    1  NOT VERIFIED               a stage failed; the first failure is the one to read
"""
from __future__ import annotations

import argparse
import http.client
import json
import re
import socket
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pi_link  # noqa: E402

G, Y, R, D, X = pi_link.G, pi_link.Y, pi_link.R, pi_link.D, pi_link.X
HELLO_KEYS = ("boot_id", "t_mono_base", "t_wall_base", "t_mono_now")     # docs/22 §3: the clock pairing
OLD_BOOT = "0ldb00t:5000"                                                # an id from a boot that never was


@dataclass
class Stage:
    name: str
    ok: bool | None                       # None = skipped
    detail: str
    data: dict = field(default_factory=dict)


class Fail(Exception):
    pass


# ── SSE, by the spec ──────────────────────────────────────────────────────────────
class SSE:
    """One GET /events. Iterating yields dicts as they are DISPATCHED (at the blank line):
        {"kind": "event", "event": name, "id": str|None, "data": str, "t": monotonic}
        {"kind": "comment", "text": ..., "t": ...}      a line starting with ':'
        {"kind": "retry", "ms": int, "t": ...}
    A read that waits longer than `stall_s` raises Fail: silence past that is a dead link."""

    def __init__(self, host: str, port: int, path: str = "/events", last_id: str | None = None,
                 stall_s: float = 5.0, connect_s: float = 5.0):
        self.t_open = time.monotonic()
        self.conn = http.client.HTTPConnection(host, port, timeout=connect_s)
        headers = {"Accept": "text/event-stream", "Cache-Control": "no-cache"}
        if last_id is not None:
            headers["Last-Event-ID"] = last_id
        try:
            self.conn.request("GET", path, headers=headers)
            self.resp = self.conn.getresponse()
        except OSError as e:
            raise Fail(f"GET {path}: {e.strerror or e}") from None
        self.t_headers = time.monotonic()
        self.status, self.ctype = self.resp.status, self.resp.getheader("Content-Type", "")
        if self.conn.sock:
            self.conn.sock.settimeout(stall_s)
        self.stall_s, self.closed_by_server = stall_s, False

    def __iter__(self):
        event, eid, data = "message", None, []
        while True:
            try:
                raw = self.resp.readline()
            except (socket.timeout, TimeoutError):
                raise Fail(f"stalled: nothing for {self.stall_s:g} s, not even a keepalive") from None
            except (OSError, http.client.HTTPException) as e:
                raise Fail(f"stream broke: {type(e).__name__}: {e}") from None
            now = time.monotonic()
            if not raw:
                self.closed_by_server = True
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            if line == "":
                if data:                   # `wall` is read HERE, at arrival: a clock comparison made after
                    yield {"kind": "event", "event": event, "id": eid, "data": "\n".join(data),   # draining the
                           "t": now, "wall": time.time()}                  # stream is off by the drain, not the Pi
                event, eid, data = "message", None, []
                continue
            if line.startswith(":"):
                yield {"kind": "comment", "text": line[1:].strip(), "t": now}
                continue
            name, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if name == "event":
                event = value
            elif name == "data":
                data.append(value)
            elif name == "id":
                eid = value
            elif name == "retry" and value.isdigit():
                yield {"kind": "retry", "ms": int(value), "t": now}

    def close(self) -> None:
        try:
            self.conn.close()
        except OSError:
            pass


def split_id(eid: str) -> tuple[str, int]:
    boot, _, n = eid.rpartition(":")
    if not boot or not n.isdigit():
        raise Fail(f"id {eid!r} is not <boot_id>:<n> (docs/16 §3c)")
    return boot, int(n)


def collect(sse: SSE, seconds: float | None = None, until_n: int | None = None, max_events: int | None = None):
    """Drain a stream until the clock, an id, a count, or the server ends it. -> (events, comments, retry_ms)"""
    events, comments, retry, t0 = [], [], None, time.monotonic()
    for item in sse:
        if item["kind"] == "retry":
            retry = item["ms"]
        elif item["kind"] == "comment":
            comments.append(item)
        else:
            events.append(item)
            if max_events and len(events) >= max_events:
                break
            if until_n is not None and item["id"] and split_id(item["id"])[1] >= until_n:
                break
        if seconds is not None and time.monotonic() - t0 >= seconds:
            break
    return events, comments, retry


def check_run(events: list[dict], boot_id: str, what: str) -> list[int]:
    """Every id-bearing event: this boot, valid JSON, and n rising by exactly 1. -> the n's."""
    ns = []
    for e in events:
        try:
            json.loads(e["data"])
        except ValueError:
            raise Fail(f"{what}: `{e['event']}` data is not JSON: {e['data'][:80]!r}") from None
        if e["id"] is None:
            continue
        boot, n = split_id(e["id"])
        if boot != boot_id:
            raise Fail(f"{what}: id {e['id']} is not of boot {boot_id}")
        if ns and n != ns[-1] + 1:
            kind = "DUPLICATE" if n <= ns[-1] else f"GAP of {n - ns[-1] - 1}"
            raise Fail(f"{what}: {kind} — id {ns[-1]} was followed by {n}")
        ns.append(n)
    return ns


# ── stages ────────────────────────────────────────────────────────────────────────
def this_machine() -> set[str]:
    mine = {"127.0.0.1", "::1", "localhost"}
    try:
        out = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=4).stdout
        mine |= set(re.findall(r"inet6? ([0-9a-fA-F:.]+)", out))
    except (OSError, subprocess.SubprocessError):
        pass
    return mine


def stage_path(host: str, port: int, local: bool) -> Stage:
    data: dict = {"kind": "this-machine" if local else ("tailnet" if pi_link.is_tailnet(host) else "lan")}
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=5):
            data["tcp_connect_ms"] = round((time.monotonic() - t0) * 1000, 1)
    except OSError as e:
        why = e.strerror or str(e) or type(e).__name__
        hint = ""
        if pi_link.is_tailnet(host):
            hint = "  (is the Pi on the tailnet and online?  python scripts/pi_link.py status)"
        elif not local:
            hint = "  (different network? a LAN address only works on our own router — docs/33 §1)"
        return Stage("path", False, f"cannot open {host}:{port} — {why}{hint}", data)
    if local:
        return Stage("path", True, f"{host} is THIS laptop — tcp {data['tcp_connect_ms']} ms, no network crossed", data)
    if data["kind"] == "tailnet" and (ts := pi_link.tailscale_bin()):
        try:
            out = subprocess.run([ts, "ping", "--c", "3", "--timeout", "3s", host],
                                 capture_output=True, text=True, timeout=15).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        pongs = re.findall(r"via (DERP\([^)]+\)|[\d.:\[\]a-fA-F]+) in ([\d.]+)ms", out)
        if pongs:
            via, ms = pongs[-1][0], [float(p[1]) for p in pongs]
            data.update(via=via, direct=not via.startswith("DERP"), rtt_ms=round(min(ms), 1))
            how = f"DIRECT WireGuard ({via})" if data["direct"] else \
                f"{Y}RELAYED via {via}{X} — works, but slower; a shared network lets it go direct"
            return Stage("path", True, f"tailnet, {how}, rtt {data['rtt_ms']} ms", data)
        return Stage("path", True, f"tailnet, tcp {data['tcp_connect_ms']} ms (tailscale ping gave no pong)", data)
    return Stage("path", True, f"LAN, tcp connect {data['tcp_connect_ms']} ms", data)


def stage_healthz(host: str, port: int) -> Stage:
    t0 = time.monotonic()
    p = pi_link.probe(host, port, timeout=5)
    ms = round((time.monotonic() - t0) * 1000, 1)
    hz = p["healthz"]
    if not hz:
        return Stage("healthz", False, f"no /healthz — {p['why']}. Is robot/server.py running on the Pi?", {})
    if "boot_id" not in hz:
        return Stage("healthz", False, "answered, but with no boot_id: that is not robot/server.py", hz)
    if "events" not in hz:
        return Stage("healthz", False, "robot/server.py answers but predates /events (no `events` block): update the Pi", hz)
    note = "" if hz.get("mode") == "hardware" else f"  {Y}<- a {hz.get('mode')} robot, not the real one{X}"
    return Stage("healthz", True, f"{ms} ms · mode={hz.get('mode')} fw={hz.get('fw')} boot={hz['boot_id']} "
                                  f"cameras={hz.get('cameras')} unavailable={hz.get('unavailable')}{note}", hz)


def stage_sse(host: str, port: int, seconds: float) -> tuple[Stage, dict]:
    sse = SSE(host, port)
    try:
        if sse.status != 200:
            raise Fail(f"GET /events -> HTTP {sse.status}")
        if not sse.ctype.lower().startswith("text/event-stream"):
            raise Fail(f"Content-Type is {sse.ctype!r}, not text/event-stream: something is buffering or rewriting it")
        events, _, retry = collect(sse, seconds=seconds)
    finally:
        sse.close()
    if not events:
        raise Fail("connected, but no event arrived")
    first = events[0]
    if first["event"] != "hello" or first["id"] is not None:
        raise Fail(f"first event is `{first['event']}` (id={first['id']}): want `hello` with NO id — "
                   "it belongs to the connection, not the log")
    hello = json.loads(first["data"])
    missing = [k for k in HELLO_KEYS if k not in hello]
    if missing:
        raise Fail(f"hello lacks the clock pairing: {missing} (docs/22 §3 — the hub rejects telemetry without it)")
    ns = check_run(events[1:], hello["boot_id"], "live stream")
    if len(ns) < 3:
        raise Fail(f"only {len(ns)} logged events in {seconds:g} s — telemetry should be ~10/s")
    ttf = round((first["t"] - sse.t_open) * 1000, 1)
    # the Pi's wall clock, read off the pairing, against ours AT THE MOMENT THE HELLO ARRIVED
    # (so it reads late by the one-way latency: -0.02 s on a good link is a clock in agreement)
    pi_wall = hello["t_wall_base"] + (hello["t_mono_now"] - hello["t_mono_base"])
    skew = round(pi_wall - first["wall"], 3)
    names = sorted({e["event"] for e in events})
    tilt = [v for e in events if e["event"] == "telemetry"
            for v in (json.loads(e["data"]).get("signals") or {}).get("tilt_rate", [])]
    unfed = bool(tilt) and all(v is None for v in tilt)
    rate = round(len(ns) / max(events[-1]["t"] - events[1]["t"], 1e-6), 1)
    clock = f"clock {skew:+.3f} s"
    if abs(skew) > 2:
        clock = f"{Y}Pi clock is {skew:+.1f} s off — no NTP. The hub copes (docs/23 §8: keeps Pi intervals, laptop's date){X}"
    if unfed:                              # not a link fault: the link just carried the nulls perfectly
        clock += (f"\n               {Y}tilt_rate is null in every sample: the balance loop is not feeding the Pi "
                  f"(ROBOT_TELEMETRY_SOURCE, robot/RUNBOOK.md §1). The link is fine; every HARDWARE capture will "
                  f"be rejected until it is.{X}")
    st = Stage("sse", True, f"hello in {ttf} ms · {len(ns)} events, ids {ns[0]}..{ns[-1]} contiguous · {rate}/s · "
                            f"{'/'.join(names)} · retry={retry} · {clock}",
               {"first_event_ms": ttf, "events": len(ns), "rate_hz": rate, "names": names, "retry_ms": retry,
                "clock_skew_s": skew, "transport": hello.get("transport"), "balance_loop_feeding": not unfed})
    return st, {"boot_id": hello["boot_id"], "ns": ns}


def stage_resume(host: str, port: int, boot_id: str, ns: list[int]) -> Stage:
    k = ns[len(ns) // 2]                   # an id from the MIDDLE: the server must replay the rest, then join live
    sse = SSE(host, port, last_id=f"{boot_id}:{k}")
    try:
        events, _, _ = collect(sse, seconds=6, until_n=ns[-1] + 5)
    finally:
        sse.close()
    gaps = [e for e in events if e["event"] == "gap"]
    if gaps:
        raise Fail(f"resume from {k} answered with a gap: {gaps[0]['data']} — the log no longer held it")
    got = check_run([e for e in events if e["event"] != "hello"], boot_id, f"resume from {k}")
    if not got:
        raise Fail(f"resume from {k}: no logged events came back")
    if got[0] != k + 1:
        raise Fail(f"resume from {k}: first event was {got[0]}, want {k + 1}")
    if got[-1] <= ns[-1]:
        raise Fail(f"resume from {k}: replay stopped at {got[-1]}, never reached live (last seen {ns[-1]})")
    return Stage("resume", True, f"Last-Event-ID {k} -> {got[0]}..{got[-1]}: replayed {ns[-1] - k}, joined live, "
                                 f"no gap, no duplicate", {"from": k, "first": got[0], "last": got[-1]})


def stage_restart(host: str, port: int, boot_id: str) -> Stage:
    sse = SSE(host, port, path="/events?limit=3", last_id=OLD_BOOT)
    try:
        events, _, _ = collect(sse, seconds=6, max_events=6)
    finally:
        sse.close()
    names = [e["event"] for e in events]
    if not events or events[0]["event"] != "hello":
        raise Fail(f"stale Last-Event-ID: want a fresh hello first, got {names}")
    gap = next((json.loads(e["data"]) for e in events if e["event"] == "gap"), None)
    if not gap or gap.get("reason") != "boot_changed":
        raise Fail(f"a Last-Event-ID from another boot was NOT flagged (events: {names}). A client would resume "
                   f"across a Pi restart without knowing it had lost everything in between")
    if gap.get("boot_id") != boot_id:
        raise Fail(f"gap names boot {gap.get('boot_id')}, hello/healthz say {boot_id}")
    check_run([e for e in events if e["event"] not in ("hello", "gap")], boot_id, "after boot_changed")
    return Stage("restart", True, f"Last-Event-ID {OLD_BOOT} -> hello, gap boot_changed({boot_id}), then this boot's log", gap)


def stage_idle(host: str, port: int) -> Stage:
    sse = SSE(host, port, path="/events?types=job&heartbeat=1", stall_s=4)
    try:
        t0, beats = time.monotonic(), []
        for item in sse:
            if item["kind"] == "comment":
                beats.append(item["t"] - t0)
            if len(beats) >= 2 or time.monotonic() - t0 > 5:
                break
    finally:
        sse.close()
    if len(beats) < 2:
        raise Fail(f"a quiet stream sent {len(beats)} keepalive(s) in 5 s with ?heartbeat=1 — a dead link would look "
                   f"identical to an idle one")
    return Stage("idle", True, f"quiet stream keeps alive: `:` comments at {beats[0]:.1f} s and {beats[1]:.1f} s",
                 {"keepalives_s": [round(b, 2) for b in beats]})


def stage_soak(host: str, port: int, boot_id: str, seconds: float, max_gap_s: float) -> Stage:
    sse = SSE(host, port, stall_s=max(max_gap_s * 2, 3))
    try:
        events, _, _ = collect(sse, seconds=seconds)
        early = sse.closed_by_server
    finally:
        sse.close()
    if early:
        raise Fail(f"the server ended the stream after {events[-1]['t'] - events[0]['t']:.1f} s of a {seconds:g} s soak")
    logged = [e for e in events if e["id"]]
    ns = check_run(events[1:], boot_id, "soak")
    gaps = [b["t"] - a["t"] for a, b in zip(logged, logged[1:])]
    if not gaps:
        raise Fail("soak: nothing arrived")
    worst, p50, p99 = max(gaps), statistics.median(gaps), sorted(gaps)[int(len(gaps) * 0.99) - 1]
    data = {"seconds": seconds, "events": len(ns), "gap_p50_ms": round(p50 * 1000, 1),
            "gap_p99_ms": round(p99 * 1000, 1), "gap_max_ms": round(worst * 1000, 1)}
    if worst > max_gap_s:
        raise Fail(f"soak: a {worst:.2f} s silence mid-stream (limit {max_gap_s:g} s; telemetry is every 100 ms). "
                   f"p50 {p50 * 1000:.0f} ms p99 {p99 * 1000:.0f} ms — the link is stalling")
    return Stage("soak", True, f"{seconds:g} s held · {len(ns)} events, every id accounted for · arrival gap "
                               f"p50 {p50 * 1000:.0f} ms  p99 {p99 * 1000:.0f} ms  max {worst * 1000:.0f} ms", data)


def stage_ws(host: str, port: int) -> Stage:
    try:
        import asyncio

        import websockets
    except ImportError:
        return Stage("ws", None, "skipped: `websockets` not installed here (use .venv/bin/python)", {})

    async def run():
        async with websockets.connect(f"ws://{host}:{port}/stream", open_timeout=5, max_size=None) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), 5))
            n, t0 = 0, time.monotonic()
            while time.monotonic() - t0 < 2:
                msg = json.loads(await asyncio.wait_for(ws.recv(), 3))
                n += msg.get("t") == "telemetry"
            return hello, n

    try:
        hello, n = asyncio.run(run())
    except Exception as e:  # noqa: BLE001 -- every way a socket fails is the same answer here
        return Stage("ws", False, f"/stream: {type(e).__name__}: {e}", {})
    if hello.get("t") != "hello" or any(k not in hello for k in HELLO_KEYS):
        return Stage("ws", False, f"/stream opened with {hello.get('t')!r}, not a hello carrying the clock pairing", {})
    if n < 10:
        return Stage("ws", False, f"/stream: {n} telemetry messages in 2 s, want ~20", {"telemetry_2s": n})
    return Stage("ws", True, f"/stream (what telemetry/hub.py consumes): hello + {n} telemetry batches in 2 s",
                 {"telemetry_2s": n})


def stage_capture(host: str, port: int) -> Stage:
    body = json.dumps({"frames": 1, "inline": True}).encode()
    conn = http.client.HTTPConnection(host, port, timeout=60)
    t0 = time.monotonic()
    try:
        conn.request("POST", "/capture", body=body, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        raw = resp.read()
    except OSError as e:
        return Stage("capture", False, f"POST /capture: {e}", {})
    finally:
        conn.close()
    dt = time.monotonic() - t0
    try:
        doc = json.loads(raw)
    except ValueError:
        return Stage("capture", False, f"HTTP {resp.status}, body is not JSON", {})
    mb = len(raw) / 1e6
    if resp.status != 200:
        # a rejected capture is the GATE working (docs/22 §4); the bytes still crossed the link
        return Stage("capture", resp.status == 409, f"HTTP {resp.status} {doc.get('error')}: {doc.get('detail', '')[:90]} "
                     f"— the link carried it; the robot declined", {"status": resp.status, "error": doc.get("error")})
    return Stage("capture", True, f"{doc.get('capture_id')} · {len(doc.get('frames', []))} frames · {mb:.2f} MB in "
                                  f"{dt:.2f} s (includes the capture itself) · skew {doc.get('skew_ms')} ms",
                 {"capture_id": doc.get("capture_id"), "mb": round(mb, 2), "seconds": round(dt, 2)})


# ── main ──────────────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host")
    ap.add_argument("--port", type=int)
    ap.add_argument("--seconds", type=float, default=4.0, help="how long to read the live stream (default 4)")
    ap.add_argument("--soak", type=float, default=20.0, help="how long to HOLD the stream (default 20; 0 = skip)")
    ap.add_argument("--max-gap", type=float, default=1.5, help="longest silence allowed mid-soak, seconds")
    ap.add_argument("--capture", action="store_true", help="also POST /capture (fires the real cameras on hardware)")
    ap.add_argument("--no-ws", action="store_true", help="skip the /stream WebSocket stage")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    env = pi_link.read_env()
    host = a.host or env.get("PI_HOST", "")
    port = a.port or int(env.get("PI_PORT", "8080") or 8080)
    if not host:
        print(f"{R}PI_HOST is not set{X} and no --host given")
        return 1
    local = host in this_machine()
    say = (lambda *_: None) if a.json else print
    say(f"\n  ROBOT LINK  ->  {host}:{port}   ({'--host' if a.host else 'PI_LINK=' + env.get('PI_LINK', '?')})\n  " + "─" * 70)

    stages: list[Stage] = []

    def run(name: str, fn, *args):
        try:
            out = fn(*args)
            st, extra = out if isinstance(out, tuple) else (out, None)
        except Fail as e:
            st, extra = Stage(name, False, str(e)), None
        stages.append(st)
        mark = f"{G}ok  {X}" if st.ok else (f"{D}skip{X}" if st.ok is None else f"{R}FAIL{X}")
        say(f"   {mark} {st.name:8s} {st.detail}")
        return st.ok, extra

    ok, _ = run("path", stage_path, host, port, local)
    hz_ok = ok and run("healthz", stage_healthz, host, port)[0]
    if hz_ok:
        ok, ctx = run("sse", stage_sse, host, port, a.seconds)
        if ok:
            run("resume", stage_resume, host, port, ctx["boot_id"], ctx["ns"])
            run("restart", stage_restart, host, port, ctx["boot_id"])
            run("idle", stage_idle, host, port)
            if a.soak > 0:
                run("soak", stage_soak, host, port, ctx["boot_id"], a.soak, a.max_gap)
        if not a.no_ws:
            run("ws", stage_ws, host, port)
        if a.capture:
            run("capture", stage_capture, host, port)

    failed = [s for s in stages if s.ok is False]
    hz = next((s.data for s in stages if s.name == "healthz"), {})
    mode = hz.get("mode")
    if failed:
        verdict, code = f"NOT VERIFIED — {failed[0].name}: {failed[0].detail}", 1
    elif local:
        verdict, code = ("CHECKS PASSED, BUT THIS IS NOT A LINK: the target is this laptop. It proves robot/server.py "
                         "and this script. Nothing crossed a network."), 2
    else:
        what = "the real robot" if mode == "hardware" else f"a {mode} robot on another machine"
        verdict, code = f"LINK VERIFIED to {what} at {host}:{port}", 0
    colour = G if code == 0 else (Y if code == 2 else R)
    say(f"\n  {colour}{verdict}{X}\n")
    if a.json:
        print(json.dumps({"host": host, "port": port, "verdict": verdict, "exit": code, "robot_mode": mode,
                          "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                          "stages": [{"name": s.name, "ok": s.ok, "detail": re.sub(r"\033\[\d+m", "", s.detail),
                                      "data": s.data} for s in stages]}, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
