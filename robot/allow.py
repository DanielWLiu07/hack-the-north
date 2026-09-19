"""robot/allow.py — who may connect, by the address the TCP connection really came from.

Shared by robot/server.py (:8080) and robot/adapter.py (:8765): neither has real authentication on a
hostile network — the capture server has none, and the adapter's bearer token crosses the venue wifi
in clear text — so off our own router both are fenced to known peers (ROBOT_ALLOW).
"""
from __future__ import annotations

import ipaddress
import json
import logging

log = logging.getLogger("gitspace.robot")


class PeerAllowList:
    """Who may talk to the robot, by the address the TCP connection really came from. The API has no
    auth, and on it are a camera that sees people, /drive and /arm — on `0.0.0.0` that is offered to
    everyone on whatever wifi the robot joined. Binding to the tailnet address is the real fix
    (docs/16 §8); this is for when that is not possible yet. Pure ASGI, so the WebSockets and the SSE
    stream are covered too. No forwarding header is ever believed: nothing sits in front of this."""

    def __init__(self, app, allow: tuple[str, ...] = ()):
        self.app = app
        self.networks = [ipaddress.ip_network(a, strict=False) for a in allow]
        self.refused = 0

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
