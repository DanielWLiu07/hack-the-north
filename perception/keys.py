"""Credentials for the external APIs, read by web/server.py's rule (usable / parked).

Keys get parked in .env to save quota (`KEY=# parked...` beside `KEY_PARKED=<the key>`), and
python-dotenv reads `KEY=# comment` as the literal value "# comment": non-empty, so a naive
`bool(os.getenv(KEY))` sends that garbage to the real service on every call (docs/10 GAP 4).
A secret never starts with "#" or contains whitespace.
"""
from __future__ import annotations

import os


def usable(value: str | None) -> str:
    """A credential worth sending, or ""."""
    v = (value or "").strip()
    return "" if (not v or v.startswith("#") or any(c.isspace() for c in v)) else v


def credential(name: str) -> tuple[str, str | None]:
    """(value, None) when `name` is usable, else ("", why): "parked", "not set" or "unusable"."""
    v = usable(os.getenv(name))
    if v:
        return v, None
    if usable(os.getenv(f"{name}_PARKED")):
        return "", f"{name} parked"
    return "", f"{name} {'not set' if not (os.getenv(name) or '').strip() else 'unusable'}"
