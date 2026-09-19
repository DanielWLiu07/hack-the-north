"""The nav publisher: the robot on its map, for the dashboard's live map (web/API-FOR-PAGES.md, the nav section).

    POST $ROOM_WEB_URL/api/edge/event  {"event": "nav", "data": {...}}

    every ~0.5 s     pose {x, y, yaw}   ROOM frame, metres; yaw in RADIANS counter-clockwise from +x
                     status, path [[x, y]...] (room frame), goal, ready, map_gen, frame "world_z_up", at
    on change only   grid {res, bounds{xmin, xmax, ymin, ymax}, nx, ny, cells_b64}
                     freshness {block_m, ages[[seconds | -1]...]}      both drawn from grid.bounds' (xmin, ymin)

Bracket Bot's 2-D map lives in ITS area frame (robot-relative when the rectangle was defined), rotated and shifted
against the room by whatever the registration says. The page draws an axis-aligned room-frame picture, so the grid is
RESAMPLED here: every room-frame cell centre goes room -> BB world -> the area frame through roomctl/frames.py and
reads the cell it lands in (nearest); outside the rectangle is 0, unknown. Freshness is resampled the same way, onto
blocks that start at the same (xmin, ymin), because that is where the page draws them from.

"The map changed" = a new map_gen, a new registration, a new rectangle, or different cells (floor newly mapped). Block
AGES change every second and do not count: they ride along when the map is sent, and again every `fresh_every_s`
(default 10 s) so the heat on the page is not frozen at the moment the last wall was found; 0 turns that off.

Best effort, on its own daemon thread: the watch loop never waits for the web. When the web cannot be reached this
says so ONCE (and once more when it is back), not twice a second and not never.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

import numpy as np

from roomctl import frames

log = logging.getLogger("roomctl.nav_publish")
FRAME = "world_z_up"


def _area(area) -> dict | None:
    """roomctl.bb_nav.AreaMap, or GET /map's own JSON -> anchor (x, y, yaw), bounds, grid (ny, nx), res, ages, block_m."""
    if area is None:
        return None
    if isinstance(area, dict):
        a, g, f = area.get("area") or {}, area.get("grid") or {}, area.get("freshness") or {}
        if not a or not g.get("cells"):
            return None
        grid = np.frombuffer(base64.b64decode(g["cells"]), np.uint8).reshape(int(g["ny"]), int(g["nx"]))
        anchor, bounds, res = a["anchor_world"], a["bounds"], float(g["resolution_m"])
        ages, block_m = np.asarray(f.get("age_s") or [[-1.0]], float), float(f.get("block_m") or 0.5)
    else:
        anchor, bounds, grid = area.anchor_world, area.bounds, np.asarray(area.grid, np.uint8)
        res, ages, block_m = float(area.resolution_m), np.asarray(area.freshness, float), float(area.block_m)
    yaw = anchor["yaw"] if "yaw" in anchor else anchor["h"]
    return {"anchor": (float(anchor["x"]), float(anchor["y"]), float(yaw)), "bounds": {k: float(bounds[k]) for k in ("xmin", "xmax", "ymin", "ymax")},
            "grid": grid, "res": res, "ages": ages, "block_m": block_m}


def _sample(src: np.ndarray, X: np.ndarray, Y: np.ndarray, x0: float, y0: float, step: float, outside) -> np.ndarray:
    """src[row, col] at area-frame points (X, Y): row 0 = ymin, col 0 = xmin. Nearest cell; `outside` beyond it."""
    j, i = np.floor((Y - y0) / step).astype(int), np.floor((X - x0) / step).astype(int)
    ok = (i >= 0) & (i < src.shape[1]) & (j >= 0) & (j < src.shape[0])
    out = np.full(X.shape, outside, dtype=src.dtype)
    out[ok] = src[j[ok], i[ok]]
    return out


def room_map(area, T: frames.SE2) -> dict | None:
    """The area map, resampled into an axis-aligned ROOM-frame grid and freshness blocks: the two `nav` map keys."""
    a = _area(area)
    if a is None:
        return None
    b, (px, py, h) = a["bounds"], a["anchor"]
    corners = [frames.bb_to_room(frames.robot_rel_to_bb(X, Y, px, py, h) + (0.0,), T)[:2]
               for X in (b["xmin"], b["xmax"]) for Y in (b["ymin"], b["ymax"])]
    res, bm = a["res"], a["block_m"]
    # snapped OUTWARD to whole blocks, so the grid and the freshness blocks share one origin and the blocks tile it
    xmin, ymin = (math.floor(min(c[k] for c in corners) / bm) * bm for k in (0, 1))
    xmax, ymax = (math.ceil(max(c[k] for c in corners) / bm) * bm for k in (0, 1))
    nx, ny = int(round((xmax - xmin) / res)), int(round((ymax - ymin) / res))
    xmax, ymax = xmin + nx * res, ymin + ny * res

    def to_area(xs: np.ndarray, ys: np.ndarray):
        gx, gy = np.meshgrid(xs, ys)                         # rows = y (row 0 is ymin), columns = x
        P = np.stack([gx.ravel(), gy.ravel(), np.zeros(gx.size)], axis=1)
        A = frames.bb_to_robot_rel_array(frames.room_to_bb_array(P, T)[:, :2], px, py, h)
        return A[:, 0].reshape(gx.shape), A[:, 1].reshape(gx.shape)

    AX, AY = to_area(xmin + (np.arange(nx) + 0.5) * res, ymin + (np.arange(ny) + 0.5) * res)
    cells = _sample(a["grid"], AX, AY, b["xmin"], b["ymin"], res, 0)
    bnx, bny = int(math.ceil((xmax - xmin) / bm - 1e-9)), int(math.ceil((ymax - ymin) / bm - 1e-9))
    BX, BY = to_area(xmin + (np.arange(bnx) + 0.5) * bm, ymin + (np.arange(bny) + 0.5) * bm)
    ages = _sample(a["ages"], BX, BY, b["xmin"], b["ymin"], bm, -1.0)
    return {"grid": {"res": res, "bounds": {"xmin": round(xmin, 4), "xmax": round(xmax, 4), "ymin": round(ymin, 4), "ymax": round(ymax, 4)},
                     "nx": nx, "ny": ny, "cells_b64": base64.b64encode(np.ascontiguousarray(cells, np.uint8).tobytes()).decode()},
            "freshness": {"block_m": bm, "ages": [[round(float(v), 1) for v in row] for row in ages]},
            "_sig": hashlib.sha1(cells.tobytes()).hexdigest()}


def pose_event(state, T: frames.SE2) -> dict:
    """The 2 Hz part: where the robot is and where it is going, in the room frame."""
    x, y, _ = frames.bb_to_room((state.x, state.y, 0.0), T)
    yaw = math.radians(frames.bb_yaw_to_heading_room(state.h, T))          # degrees from +X -> radians ccw from +x
    path = []
    raw = getattr(state, "path", None)
    if raw and len(raw) == 2 and len(raw[0]) == len(raw[1]) and not (len(raw[0]) == 2 and isinstance(raw[0][0], (list, tuple))):
        raw = list(zip(raw[0], raw[1]))                                    # Bracket Bot sends [[x...], [y...]]
    for p in raw or []:
        rx, ry, _ = frames.bb_to_room((float(p[0]), float(p[1]), 0.0), T)
        path.append([round(rx, 3), round(ry, 3)])
    out = {"pose": {"x": round(x, 3), "y": round(y, 3), "yaw": round(yaw, 4)}, "status": state.status, "ready": bool(state.ready),
           "path": path, "map_gen": int(state.map_gen), "frame": FRAME,
           "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")}
    goal = getattr(state, "goal", None)
    if goal:
        gx, gy, _ = frames.bb_to_room((goal[0], goal[1], 0.0), T)
        out["goal"] = [round(gx, 3), round(gy, 3)]
    return out


class NavPublisher:
    def __init__(self, nav: Any, reg_provider: Callable[[], tuple | None], publish: Callable[[str, dict], Any], *,
                 hz: float = 2.0, fresh_every_s: float = 10.0, clock: Callable[[], float] = time.monotonic):
        self.nav, self.reg_provider, self.publish = nav, reg_provider, publish
        self.period, self.fresh_every_s, self.clock = 1.0 / max(hz, 0.1), float(fresh_every_s), clock
        self.sent = self.maps_sent = self.failures = 0
        self._map_key = None
        self._map_at = -1e18
        self._down, self._pending = False, None
        self._stop, self._thread = threading.Event(), None

    def build(self) -> dict | None:
        """One event, or None while there is nothing true to say (no pose, SLAM not ready is still a pose: `ready`
        says so; no registration, or one for another map: the pose could not be put in the room frame)."""
        st = getattr(self.nav, "state", None)
        got = self.reg_provider() if self.reg_provider else None
        if st is None or not got:
            return None
        T, gen = got if isinstance(got, tuple) else (got, None)
        if gen is not None and gen != st.map_gen:
            return None
        ev = pose_event(st, T)
        m = room_map(getattr(self.nav, "area", None), T)
        now = self.clock()
        if m is not None:
            key = (st.map_gen, round(T.theta, 6), round(T.tx, 4), round(T.ty, 4), m.pop("_sig"), m["grid"]["nx"], m["grid"]["ny"])
            due = self.fresh_every_s > 0 and now - self._map_at >= self.fresh_every_s
            if key != self._map_key or due:
                ev.update(m)
                self._pending = (key, now)
        return ev

    def once(self) -> bool:
        """Build and send one event. True if it went out."""
        self._pending = None
        ev = self.build()
        if ev is None:
            return False
        try:
            self.publish("nav", ev)
        except Exception as e:  # noqa: BLE001  the web being away is not this loop's problem, but it is worth ONE line
            self.failures += 1
            if not self._down:
                self._down = True
                log.warning("nav: the dashboard is not reachable (%s: %s). Still trying, quietly.", type(e).__name__, e)
            return False
        self.sent += 1
        if self._pending:                                  # only a map that ARRIVED counts as sent
            self._map_key, self._map_at = self._pending
            self.maps_sent += 1
        if self._down:
            self._down = False
            log.warning("nav: the dashboard is reachable again after %d failed sends.", self.failures)
            self._map_key = None                           # it may have restarted with nothing kept: send the map again
        return True

    def start(self) -> "NavPublisher":
        def run():
            while not self._stop.is_set():
                t0 = time.monotonic()
                try:
                    self.once()
                except Exception as e:  # noqa: BLE001  a bug here must not take the watch loop's process down
                    log.warning("nav: could not build an event (%s: %s)", type(e).__name__, e)
                self._stop.wait(max(0.0, self.period - (time.monotonic() - t0)))
        self._thread = threading.Thread(target=run, daemon=True, name="nav-publisher")
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2.0)
