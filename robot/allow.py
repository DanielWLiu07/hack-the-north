"""robot/allow.py — who may connect, by the address the TCP connection really came from.

Shared by robot/server.py (:8080) and robot/adapter.py (:8765): neither has real authentication on a
hostile network — the capture server has none, and the adapter's bearer token crosses the venue wifi
in clear text — so off our own router both are fenced to known peers (ROBOT_ALLOW).

`parse()` reads the list itself rather than trusting whoever loaded the environment. Measured: an
inline comment on the ROBOT_ALLOW line is stripped by python-dotenv when the server is hand-started
and NOT stripped by systemd's EnvironmentFile, so the same file meant two different things under
the two launchers — and the comment arrived inside the value.

A bad entry NEVER raises here. It used to, and because a middleware is built per request that meant
500 on every route while the process stayed up and looked alive: neither open nor closed, just
broken in a way nothing reports. Now a bad entry is logged, named on /healthz, and dropped; if that
empties a list that was asked for, the fence closes to LOOPBACK ONLY — never to everyone — so
someone on the robot can still `curl 127.0.0.1:8080/healthz` and read what is wrong.
"""
from __future__ import annotations

import ipaddress
import json
import logging

log = logging.getLogger("gitspace.robot")

LOOPBACK = ("127.0.0.1/32", "::1/128")


def parse(spec) -> tuple[list, list[str]]:
    """"127.0.0.1, 10.0.0.5/32  # a comment" -> ([networks], [what could not be read])."""
    if not isinstance(spec, str):
        spec = ",".join(str(x) for x in (spec or ()))
    spec = spec.split("#", 1)[0]                   # an inline comment, whichever loader left it in
    nets, bad = [], []
    for entry in (e.strip().strip("\"'") for e in spec.split(",")):
        if not entry:
            continue
        try:
            nets.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            bad.append(entry)
    return nets, bad


class PeerAllowList:
    """Who may talk to the robot, by the address the TCP connection really came from. The API has no
    auth, and on it are a camera that sees people, /drive and /arm — on `0.0.0.0` that is offered to
    everyone on whatever wifi the robot joined. Binding to the tailnet address is the real fix
    (docs/16 §8); this is for when that is not possible yet. Pure ASGI, so the WebSockets and the SSE
    stream are covered too. No forwarding header is ever believed: nothing sits in front of this."""

    def __init__(self, app, allow: tuple[str, ...] | str = ()):
        self.app = app
        self.networks, self.invalid = parse(allow)
        self.refused = 0
        if self.invalid:
            log.error("ROBOT_ALLOW: cannot read %s — dropped. %s", self.invalid,
                      "Closing to loopback only" if not self.networks else "The rest of the list still applies")
            if not self.networks:                  # asked for a fence and got none: close it, do not open it
                self.networks = [ipaddress.ip_network(n) for n in LOOPBACK]

    def state(self) -> dict:
        """For /healthz, which is reachable from the robot itself even when the list is wrong."""
        return {"networks": [str(n) for n in self.networks], "invalid": self.invalid, "refused": self.refused}

    def permits(self, host: str) -> bool:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False                           # no address, no entry
        ip = getattr(ip, "ipv4_mapped", None) or ip
        return any(ip in n for n in self.networks)

    async def __call__(self, scope, receive, send):
        if self.networks and scope["type"] in ("http", "websocket"):
            host = (scope.get("client") or ("", 0))[0]
            if not self.permits(host):
                self.refused += 1
                if self.refused in (1, 10, 100, 1000):
                    log.warning("refused %s (%d so far): not in ROBOT_ALLOW", host or "?", self.refused)
                if scope["type"] == "websocket":
                    return await send({"type": "websocket.close", "code": 1008})
                body = json.dumps({"error": "forbidden", "detail": "this address is not in ROBOT_ALLOW",
                                   "retryable": False}).encode()
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
                return await send({"type": "http.response.body", "body": body})
        await self.app(scope, receive, send)
