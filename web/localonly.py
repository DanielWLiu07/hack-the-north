"""localonly.py — ONE definition of "this request came from this laptop", for everything that must not leave it:
camera frames under landing/live/, the event inlet, starting a billed Seer run.

A tunnel (cloudflared, Tailscale Funnel, Vercel -> Caddy) connects from 127.0.0.1 too, so a loopback peer is not
enough: the request must ALSO carry no forwarding header. Same rule as robot_view_api.py."""
from __future__ import annotations

from typing import Iterable

FORWARDED = ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded", "x-real-ip", "cf-connecting-ip",
             "cf-ray", "cdn-loop", "true-client-ip", "x-vercel-forwarded-for", "tailscale-user-login",
             "tailscale-funnel-request", "via")
LOOPBACK = ("127.0.0.1", "::1")


def is_local(peer: str | None, header_names: Iterable[str | bytes]) -> bool:
    names = {(n.decode("latin-1") if isinstance(n, bytes) else n).lower() for n in header_names}
    return (peer or "") in LOOPBACK and not any(h in names for h in FORWARDED)
