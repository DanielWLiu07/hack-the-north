#!/usr/bin/env python3
"""pi_link.py — which address the laptop uses for the robot: the LAN one or the tailnet one.

Everything that talks to the Pi reads PI_HOST / PI_PORT from .env (roomctl/robot_client.py,
telemetry/hub.py, the bootstrap). This does not change that. It keeps BOTH address pairs in
.env and repoints the three live keys at one of them, so switching networks is one command
and no consumer ever learns there were two:

    PI_LINK=tailnet                      which pair the live keys mirror   (written here)
    PI_HOST / LAPTOP_IP / RERUN_ADDR     the live keys — what everything reads
    PI_HOST_LAN     / LAPTOP_IP_LAN      our own router, static IPs (docs/16 §8)
    PI_HOST_TAILNET / LAPTOP_IP_TAILNET  Tailscale 100.x addresses (docs/33)

    python scripts/pi_link.py status          what is selected, and what actually answers
    python scripts/pi_link.py discover        find `bracketbot` on the tailnet, record its 100.x
    python scripts/pi_link.py use tailnet     (or: use lan)
    python scripts/pi_link.py set lan 192.168.0.124 [--laptop IP]   the robot moved networks: record the pair, select it
    python scripts/pi_link.py auto            select whichever answers; tailnet first

LAPTOP_IP moves with PI_HOST because the Pi streams Rerun TO the laptop over the same link:
a tailnet PI_HOST with a LAN RERUN_ADDR is a robot logging to an address it cannot reach.
The Pi's own .env needs the matching RERUN_ADDR — `status` prints it.

PI_HOST is always an IP, never a MagicDNS name. On this laptop (open-source tailscaled) macOS
does not route *.ts.net to Tailscale's resolver: `bracketbot` does not resolve at all, and a
Funnel'd name resolves to a PUBLIC ingress address. `discover` asks tailscaled directly instead.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"
TAILNET = ipaddress.ip_network("100.64.0.0/10")        # Tailscale's CGNAT range
PI_NAME = "bracketbot"                                 # --hostname in provision_pi_tailscale.sh
MODES = ("lan", "tailnet")
PAIR_KEYS = ("PI_HOST_LAN", "LAPTOP_IP_LAN", "PI_HOST_TAILNET", "LAPTOP_IP_TAILNET", "PI_LINK")
HEADER = "both address pairs"
AFTER = "RERUN_ADDR"                                   # new keys are inserted below this line

G, Y, R, D, X = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    G = Y = R = D = X = ""


# ── .env: read, and change ONLY the named keys ────────────────────────────────────
def _split(line: str) -> tuple[str, str, str] | None:
    """'KEY=value   # note' -> (KEY, value, '   # note'). None for anything else."""
    m = re.match(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)=(.*)$", line)
    if not m:
        return None
    key, rest = m.group(1), m.group(2)
    c = re.search(r"\s+#.*$", rest)
    value, note = (rest[:c.start()], rest[c.start():]) if c else (rest, "")
    return key, value.strip().strip("\"'"), note


def read_env(path: Path | None = None) -> dict[str, str]:
    path = path or ENV                                 # at CALL time: a `= ENV` default is frozen at def
    out: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            kv = _split(line)
            if kv:
                out[kv[0]] = kv[1]
    return out


def write_env(changes: dict[str, str], path: Path | None = None) -> list[str]:
    """Set keys in place, keeping each line's trailing comment; add missing ones under the
    network block. Every other byte of the file is untouched. -> the keys that changed."""
    path = path or ENV
    lines = path.read_text().splitlines(keepends=True)
    todo, changed, anchor = dict(changes), [], None
    for i, line in enumerate(lines):
        kv = _split(line)
        if not kv:
            continue
        key, value, note = kv
        if key == AFTER:
            anchor = i
        if key in todo:
            new = todo.pop(key)
            if new != value:
                pad = note or ""
                lines[i] = f"{key}={new}{pad}\n"
                changed.append(key)
    if todo:                                           # keys .env has never had
        at = (anchor + 1) if anchor is not None else len(lines)
        lines[at:at] = [f"{k}={v}\n" for k, v in todo.items()]
        changed += list(todo)
    if not any(HEADER in ln for ln in lines):          # say what the pair block is, once
        first = next((i for i, ln in enumerate(lines) if (kv := _split(ln)) and kv[0] in PAIR_KEYS), None)
        if first is not None:
            lines.insert(first, f"# {HEADER}; PI_HOST / LAPTOP_IP / RERUN_ADDR above mirror ONE of them. "
                                "Switch with scripts/pi_link.py, never by hand (docs/33)\n")
            changed.append("#header")
    if not changed:
        return []
    backup = path.with_name(".env.prelink")            # `.env.*` is gitignored; first edit only
    if not backup.exists():
        shutil.copy2(path, backup)
        os.chmod(backup, 0o600)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".env.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.writelines(lines)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)                          # atomic: never a half-written .env
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return changed


# ── addresses ─────────────────────────────────────────────────────────────────────
def is_ip(s: str) -> bool:
    try:
        ipaddress.ip_address(s)
        return True
    except ValueError:
        return False


def is_tailnet(s: str) -> bool:
    return is_ip(s) and ipaddress.ip_address(s) in TAILNET


def tailscale_bin() -> str | None:
    for c in (shutil.which("tailscale"), "/opt/homebrew/bin/tailscale", "/usr/local/bin/tailscale",
              "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        if c and os.path.exists(c):
            return c
    return None


def tailscale_json() -> dict | None:
    ts = tailscale_bin()
    if not ts:
        return None
    try:
        out = subprocess.run([ts, "status", "--json"], capture_output=True, text=True, timeout=6)
        return json.loads(out.stdout) if out.returncode == 0 else None
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


def tailnet_self(st: dict | None) -> str:
    ips = ((st or {}).get("Self") or {}).get("TailscaleIPs") or []
    return next((i for i in ips if is_tailnet(i)), "")


def tailnet_peers(st: dict | None, name: str = PI_NAME) -> list[dict]:
    """Peers whose hostname is `name` (or name-1, name-2: what a re-registered node becomes)."""
    hits = []
    for p in ((st or {}).get("Peer") or {}).values():
        host = (p.get("HostName") or "").lower()
        if host == name or re.fullmatch(re.escape(name) + r"-\d+", host):
            ip = next((i for i in p.get("TailscaleIPs") or [] if is_tailnet(i)), "")
            hits.append({"host": host, "ip": ip, "online": bool(p.get("Online")),
                         "relay": p.get("Relay") or "", "direct": p.get("CurAddr") or "",
                         "last_seen": p.get("LastSeen") or ""})
    return sorted(hits, key=lambda h: (not h["online"], h["host"]))


def probe(host: str, port: str | int, timeout: float = 2.5) -> dict:
    """-> {'tcp': bool, 'healthz': dict|None, 'why': str}. /healthz is robot/server.py's."""
    if not host:
        return {"tcp": False, "healthz": None, "why": "not set"}
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            pass
    except OSError as e:
        return {"tcp": False, "healthz": None, "why": e.strerror or str(e) or type(e).__name__}
    try:
        # "-0" = parent says unsampled: a health probe must not become a Sentry transaction on the robot (obs.py
        # samples at 1.0 with no route filter; watch_robot_link.sh asks every 5 s, all day)
        req = urllib.request.Request(f"http://{host}:{port}/healthz",
                                     headers={"sentry-trace": f"{uuid.uuid4().hex}-{uuid.uuid4().hex[:16]}-0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read() or b"{}")
            return {"tcp": True, "healthz": body if isinstance(body, dict) else None, "why": ""}
    except (urllib.error.URLError, ValueError, OSError) as e:
        return {"tcp": True, "healthz": None, "why": f"port open, /healthz: {e}"}


def rerun_addr(current: str, laptop_ip: str) -> str:
    """Swap only the host in rerun+http://<host>:9876/proxy."""
    if not current:
        return f"rerun+http://{laptop_ip}:9876/proxy"
    return re.sub(r"(://)([^:/]+)", lambda m: m.group(1) + laptop_ip, current, count=1)


def seed(env: dict[str, str], st: dict | None) -> dict[str, str]:
    """First run: .env has only the live keys. File them under the pair they belong to."""
    add: dict[str, str] = {}
    host, lip = env.get("PI_HOST", ""), env.get("LAPTOP_IP", "")
    if "PI_HOST_LAN" not in env:
        add["PI_HOST_LAN"] = "" if is_tailnet(host) else host
    if "LAPTOP_IP_LAN" not in env:
        add["LAPTOP_IP_LAN"] = "" if is_tailnet(lip) else lip
    if "PI_HOST_TAILNET" not in env:
        add["PI_HOST_TAILNET"] = host if is_tailnet(host) else ""
    if not env.get("LAPTOP_IP_TAILNET"):
        add["LAPTOP_IP_TAILNET"] = tailnet_self(st)
    if "PI_LINK" not in env:
        add["PI_LINK"] = "tailnet" if is_tailnet(host) else "lan"
    return add


# ── commands ──────────────────────────────────────────────────────────────────────
def cmd_status(env: dict[str, str], st: dict | None) -> int:
    port = env.get("PI_PORT", "8080")
    live = env.get("PI_HOST", "")
    mode = env.get("PI_LINK") or ("tailnet" if is_tailnet(live) else "lan")
    print(f"\n  PI_LINK={mode}   PI_HOST={live or '(unset)'}   LAPTOP_IP={env.get('LAPTOP_IP', '')}\n")
    answered = None
    for m in MODES:
        host = env.get(f"PI_HOST_{m.upper()}", "") or (live if (is_tailnet(live) == (m == "tailnet")) else "")
        p = probe(host, port)
        mark = f"{G}ANSWERS{X}" if p["healthz"] else (f"{Y}PORT OPEN{X}" if p["tcp"] else f"{R}no{X}")
        sel = "<- selected" if m == mode else ""
        hz = p["healthz"] or {}
        detail = (f"mode={hz.get('mode')} fw={hz.get('fw')} cameras={hz.get('cameras')}" if hz else p["why"])
        print(f"   {m:8s} {host or '(not set)':16s} {mark:20s} {D}{detail}{X}  {sel}")
        if p["healthz"] and answered is None:
            answered = m
    peers = tailnet_peers(st)
    if st is None:
        print(f"\n   {Y}tailscale is not running on this laptop{X}")
    elif not peers:
        print(f"\n   tailnet: no node named `{PI_NAME}` — the Pi has not joined (docs/33 §2)")
    for h in peers:
        path = (f"direct {h['direct']}" if h["direct"] else f"relayed via DERP({h['relay']})") if h["online"] else \
            f"offline, last seen {h['last_seen'][:16]}"
        print(f"\n   tailnet: {h['host']} {h['ip']}  {path}")
    lip = env.get(f"LAPTOP_IP_{mode.upper()}") or env.get("LAPTOP_IP", "")
    print(f"\n   the Pi's own .env needs:  RERUN_ADDR={rerun_addr(env.get('RERUN_ADDR', ''), lip)}")
    if answered and answered != mode:
        print(f"\n   {Y}{answered} answers but {mode} is selected:{X}  python scripts/pi_link.py use {answered}")
    print()
    return 0 if answered == mode else 1


def cmd_discover(env: dict[str, str], st: dict | None, name: str) -> int:
    if st is None:
        print(f"{R}tailscale isn't running here{X} — start it, then retry")
        return 2
    peers = [h for h in tailnet_peers(st, name) if h["ip"]]
    if not peers:
        print(f"no tailnet node named `{name}`. The Pi has not joined yet — run\n"
              f"  ./scripts/provision_pi_tailscale.sh            (docs/33 §2)\n"
              f"or, if it joined under another name:  python scripts/pi_link.py discover --name <hostname>")
        return 1
    online = [h for h in peers if h["online"]]
    if len(online) > 1:
        print(f"{Y}{len(online)} online nodes match `{name}`{X} — a re-provisioned Pi leaves its old node behind. "
              f"Delete the stale one at login.tailscale.com/admin/machines. Using {online[0]['host']}.")
    pick = (online or peers)[0]
    changed = write_env({"PI_HOST_TAILNET": pick["ip"], "LAPTOP_IP_TAILNET": tailnet_self(st)})
    state = "online" if pick["online"] else f"{Y}OFFLINE{X} (last seen {pick['last_seen'][:16]})"
    print(f"{pick['host']} is {pick['ip']} — {state}.  {'recorded: ' + ', '.join(changed) if changed else 'already recorded'}")
    print("next:  python scripts/pi_link.py use tailnet")
    return 0 if pick["online"] else 1


def cmd_use(env: dict[str, str], mode: str) -> int:
    host, lip = env.get(f"PI_HOST_{mode.upper()}", ""), env.get(f"LAPTOP_IP_{mode.upper()}", "")
    if not host:
        hint = "python scripts/pi_link.py discover" if mode == "tailnet" else "set PI_HOST_LAN in .env"
        print(f"{R}PI_HOST_{mode.upper()} is empty{X} — {hint}")
        return 2
    if not is_ip(host):
        print(f"{R}PI_HOST_{mode.upper()}={host} is a name{X}; it must be an IP (see this file's docstring)")
        return 2
    new = {"PI_LINK": mode, "PI_HOST": host}
    if lip:
        new["LAPTOP_IP"] = lip
        new["RERUN_ADDR"] = rerun_addr(env.get("RERUN_ADDR", ""), lip)
    else:
        print(f"{Y}LAPTOP_IP_{mode.upper()} is empty{X}: LAPTOP_IP / RERUN_ADDR left as they were")
    changed = write_env(new)
    print(f"PI_LINK={mode}  PI_HOST={host}" + (f"  LAPTOP_IP={lip}" if lip else "") +
          f"   ({', '.join(changed) + ' changed' if changed else 'no change'})")
    p = probe(host, env.get("PI_PORT", "8080"))
    if p["healthz"]:
        print(f"{G}the Pi answers there{X} (mode={p['healthz'].get('mode')}). "
              f"Restart anything already running — processes read .env once at start.")
    else:
        print(f"{Y}nothing answers at {host} yet{X} ({p['why']}). Selected anyway.")
    return 0


def cmd_set(env: dict[str, str], mode: str, host: str, laptop: str | None) -> int:
    """Record a pair's addresses (the robot moved networks, or joined a new router) and select it — the one command for
    docs/33's "never edit .env by hand". The laptop's address defaults to the one the route to the robot leaves from."""
    if not is_ip(host):
        print(f"{R}{host} is not an IP{X}; PI_HOST is always an IP (see this file's docstring)")
        return 2
    if not laptop:
        import socket
        try:
            sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); sk.connect((host, 9)); laptop = sk.getsockname()[0]; sk.close()
        except OSError:
            laptop = ""
    new = {f"PI_HOST_{mode.upper()}": host}
    if laptop:
        new[f"LAPTOP_IP_{mode.upper()}"] = laptop
    write_env(new)
    print(f"PI_HOST_{mode.upper()}={host}" + (f"  LAPTOP_IP_{mode.upper()}={laptop}" if laptop else ""))
    return cmd_use(read_env(), mode)


def cmd_auto(env: dict[str, str]) -> int:
    port = env.get("PI_PORT", "8080")
    for mode in ("tailnet", "lan"):                    # tailnet first: it is the one that survives a network change
        host = env.get(f"PI_HOST_{mode.upper()}", "")
        if host and probe(host, port)["healthz"]:
            return cmd_use(env, mode)
    print(f"{R}the Pi answers on neither link.{X}  python scripts/pi_link.py status")
    return 1


def warn_interpreter(root: Path | None = None) -> bool:
    """Say once, at a script's front door, when it is not running under the repo's own .venv — and return whether it is.
    Every number out of this pipeline is reproducible only there (2026-09-20: system python on this laptop had cv2 5.0 vs
    .venv's 4.14 in the stereo chain, older ultralytics/torch, and no elasticsearch or open3d at all; names, the alignment
    count and search results all differed or quietly degraded). The check is `sys.prefix` relative to .venv — NOT
    realpath of the interpreter: .venv/bin/python is a SYMLINK to the system binary, so realpath compares equal and a
    guard built on it never fires (found by gitspace-22). A warning, not a refusal."""
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    venv = root / ".venv"
    if not venv.is_dir():
        return True
    ok = Path(sys.prefix).resolve().is_relative_to(venv.resolve())
    if not ok:
        print(f"\033[33mnot the repo's .venv\033[0m ({sys.prefix}): any number out of this pipeline — object NAMES, the alignment "
              f"count, search results — can differ or silently degrade under another interpreter (cv2 / ultralytics / torch "
              f"versions, packages missing). Use  {venv}/bin/python {Path(sys.argv[0]).name} …", file=sys.stderr)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status")
    d = sub.add_parser("discover")
    d.add_argument("--name", default=PI_NAME)
    u = sub.add_parser("use")
    u.add_argument("mode", choices=MODES)
    st_ = sub.add_parser("set", help="record a pair's addresses and select it: set lan 192.168.0.124 [--laptop 192.168.0.30]")
    st_.add_argument("mode", choices=MODES); st_.add_argument("host"); st_.add_argument("--laptop", default=None)
    sub.add_parser("auto")
    a = ap.parse_args()
    if not ENV.exists():
        print(f"{R}{ENV} does not exist{X} — cp .env.example .env")
        return 2
    st = tailscale_json()
    add = seed(read_env(), st)
    if add:
        write_env(add)
    env = read_env()
    if a.cmd == "discover":
        return cmd_discover(env, st, a.name.lower())
    if a.cmd == "use":
        return cmd_use(env, a.mode)
    if a.cmd == "set":
        return cmd_set(env, a.mode, a.host, a.laptop)
    if a.cmd == "auto":
        return cmd_auto(env)
    return cmd_status(env, st)


if __name__ == "__main__":
    sys.exit(main())
