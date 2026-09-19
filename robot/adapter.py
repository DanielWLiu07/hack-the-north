#!/usr/bin/env python3
"""robot/adapter.py — the robot side of the Housebot Edge contract (PLAN §0), on 127.0.0.1:8765.

    GET  /health                          {"ok": true, "version": 1}                       (no token)
    GET  /v1/observation?request_id=…     {version, request_id, observation: WorldState}
    POST /v1/actions                      {version, request_id, action} -> {version, request_id, result}
    GET  /registration                    T_bb<-room, as measured — what perception must use too

The wire is the edge's own (`gitirl` src/gitirl_agent/robot/http_contract.py @ 9582081): version 1,
the same status codes and error documents as its template server, `Authorization: Bearer
$HOUSEBOT_ROBOT_TOKEN`. `result.status` is success | failed | retryable, and execute() answers only
at a terminal state.

First capability: POINT_AT_OBJECT — go and stand STANDOFF_M from the object, facing it, and point.
The target arrives in the ROOM frame and the standoff is planned there; it crosses into Bracket
Bot's frame ONCE, through roomctl/frames.py (the project's single conversion — this file has no
sin/cos). A target in any other `coordinate_frame` is refused rather than guessed at.

    python -m robot.adapter --sim         # simulated base + arm: the whole chain, no robot
    python -m robot.adapter               # hardware: every motion is REFUSED (status "failed")

Hardware motion is not built, deliberately: driving a balancing robot and moving an arm need BB
nav brought up and the arm proven by a person at the robot (Gate 1), and a measured registration.
Until then this answers honestly instead of pretending to have pointed at anything.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

import obs  # noqa: E402
from robot import frames  # noqa: E402
from robot.allow import PeerAllowList  # noqa: E402

API_VERSION = 1                       # the edge's ROBOT_API_VERSION; a mismatch is a 422, both ways
ACTIONS = ("OBSERVE", "MOVE_OBJECT", "PICK_OBJECT", "PLACE_OBJECT", "POINT_AT_OBJECT", "VERIFY_OBJECT", "WAIT", "NO_OP")
STANDOFF_M = 0.60                     # stand this far from the object: inside the arm's reach, outside the desk
MAX_REQUEST_BYTES = 1024 * 1024
SIM_REGISTRATION = frames.Registration(frames.SE2(math.radians(90), 1.0, -0.5, 0.0), "sim-map-1", 0.0, "simulated")
# ^ deliberately NOT the identity: a conversion that is skipped must fail a test, not pass by luck


class Refused(Exception):
    """A terminal `failed`: asking again will not help."""


class ContractError(ValueError):
    pass


def err(status: int, code: str, detail: str, retryable: bool = False) -> JSONResponse:
    return JSONResponse({"error": code, "detail": detail, "retryable": retryable}, status_code=status)


def result(request_id: str, status: str, message: str, observations: dict | None = None) -> dict:
    return {"version": API_VERSION, "request_id": request_id,
            "result": {"status": status, "message": message, "observations": observations}}


# ── the plan: where to stand, which way to face ──────────────────────────────────
@dataclass(frozen=True)
class PointPlan:
    object_room: tuple[float, float, float]
    stand_room: tuple[float, float]
    heading_room_deg: float           # degrees from room +X, CCW
    navigate: dict                    # POST :8020/navigate's body, BB world frame (roomctl.frames)
    object_bb: tuple[float, float, float]


@dataclass(frozen=True)
class _Base:                          # the shape roomctl.frames.base_pose_to_navigate takes
    x: float
    y: float
    yaw: float


def plan_point(target: dict, reg: frames.Registration, robot_room: tuple[float, float]) -> PointPlan:
    """Planned in the ROOM frame — approach along the line from where the robot is, stop STANDOFF_M
    short, face the object — then converted once. atan2/hypot here are room geometry, not a frame change."""
    meta = target.get("metadata") or {}
    frame = meta.get("coordinate_frame")
    if frame != frames.ROOM_FRAME:
        raise Refused(f"target.metadata.coordinate_frame is {frame!r}; only {frames.ROOM_FRAME!r} is accepted — "
                      "a frame is never guessed")
    pos = target.get("position")
    try:
        x, y, z = (float(pos[k]) for k in ("x", "y", "z"))
    except (TypeError, KeyError, ValueError):
        raise Refused("target.position needs numeric x, y, z (metres, room frame)") from None
    if not all(math.isfinite(v) for v in (x, y, z)):
        raise Refused("target.position is not finite")
    dx, dy = x - robot_room[0], y - robot_room[1]
    d = math.hypot(dx, dy)
    if d < 1e-6:
        raise Refused("the robot is standing on the target")
    k = max(0.0, d - STANDOFF_M) / d
    stand = (robot_room[0] + dx * k, robot_room[1] + dy * k)
    heading = math.degrees(math.atan2(dy, dx))
    return PointPlan((x, y, z), stand, heading, frames.base_pose_to_navigate(_Base(stand[0], stand[1], heading), reg.T),
                     frames.room_to_bb((x, y, z), reg.T))


# ── backends ─────────────────────────────────────────────────────────────────────
class SimBackend:
    """A base and an arm that do what they are told, instantly-ish, in BB's frame. NOT a robot."""
    simulated = True

    def __init__(self, time_scale: float = 1.0):
        self.pose_bb, self.time_scale = (0.0, 0.0, 0.0), time_scale
        self.log: list[tuple] = []
        self._lock = threading.Lock()

    def navigate(self, x: float, y: float, yaw: float) -> None:
        d = math.hypot(x - self.pose_bb[0], y - self.pose_bb[1])
        time.sleep(min(2.0, d / 0.5) * self.time_scale)
        self.pose_bb = (x, y, yaw)
        self.log.append(("navigate", round(x, 3), round(y, 3), round(yaw, 4)))

    def point_at(self, x: float, y: float, z: float) -> None:
        time.sleep(0.5 * self.time_scale)
        self.log.append(("point", round(x, 3), round(y, 3), round(z, 3)))


class HardwareBackend:
    """Refuses every motion. BB nav (bbapps/nav on :8020) and the arm through bbos are Gate 1's to
    bring up and prove, by a person at the robot; nothing here may open a bbos writer before that."""
    simulated = False
    pose_bb = (0.0, 0.0, 0.0)

    def navigate(self, *a) -> None:
        raise Refused("hardware motion is not enabled: BB nav is not wired (Gate 1 items 2-3); run --sim for the chain")

    def point_at(self, *a) -> None:
        raise Refused("hardware motion is not enabled: no arm motion through bbos has been proven (Gate 1 item 4)")


# ── the app ──────────────────────────────────────────────────────────────────────
def create_app(backend=None, registration: frames.Registration | None = None, token: str | None = None,
               allow: tuple[str, ...] = ()) -> FastAPI:
    obs.init("robot-adapter")                       # before FastAPI(): the integration continues sentry-trace for us
    backend = backend or HardwareBackend()
    if registration is None:
        registration = SIM_REGISTRATION if backend.simulated else frames.from_env()
    app = FastAPI(title="gitspace robot adapter", version=str(API_VERSION))
    app.add_middleware(PeerAllowList, allow=allow)
    app.state.backend, app.state.registration = backend, registration
    busy = threading.Lock()

    def authorised(request: Request) -> bool:
        return token is None or request.headers.get("authorization") == f"Bearer {token}"

    @app.get("/health")
    async def health():
        return {"ok": True, "version": API_VERSION, "simulated": backend.simulated,
                "registration": "simulated" if registration and registration.source == "simulated"
                else "measured" if registration else "unmeasured"}

    @app.get("/registration")
    async def registration_doc(request: Request):
        if not authorised(request):
            return err(401, "unauthorized", "invalid token")
        if registration is None:
            return err(503, "registration_unmeasured", "T_bb<-room has not been measured (ROBOT_REGISTRATION): "
                       "there is no default, and the identity would be a confident lie")
        return registration.document()

    @app.get("/v1/observation")
    async def observation(request: Request, request_id: str = ""):
        if not authorised(request):
            return err(401, "unauthorized", "invalid token")
        if not request_id:
            return err(422, "bad_request", "request_id is required")
        x, y, yaw = backend.pose_bb
        meta = {"simulated": backend.simulated, "robot_pose_bb": {"x": x, "y": y, "yaw": yaw}}
        if registration is not None:
            rx, ry, _ = frames.bb_to_room((x, y, 0.0), registration.T)
            meta["robot_pose_room"] = {"x": round(rx, 4), "y": round(ry, 4), "coordinate_frame": frames.ROOM_FRAME,
                                       "yaw_deg": round(frames.bb_yaw_to_heading_room(yaw, registration.T), 3)}
        return {"version": API_VERSION, "request_id": request_id, "observation": {"objects": [], "metadata": meta}}

    @app.post("/v1/actions")
    async def actions(request: Request):
        if not authorised(request):
            return err(401, "unauthorized", "invalid token")
        raw = await request.body()
        if not raw or len(raw) > MAX_REQUEST_BYTES:
            return err(400, "bad_json", "request body size is invalid")
        try:
            doc = json.loads(raw)
        except ValueError:
            return err(400, "bad_json", "body must be valid JSON")
        try:
            action = _validate(doc)
        except ContractError as e:
            return err(422, "bad_request", str(e))
        rid = action["request_id"]
        import asyncio
        try:
            return await asyncio.to_thread(_execute, action, rid)
        except Exception as e:  # noqa: BLE001 -- the template's rule: backend errors become explicit API errors
            return err(503, "execution_failed", f"{type(e).__name__}: {e}")

    def _validate(doc) -> dict:
        if not isinstance(doc, dict):
            raise ContractError("response must be a JSON object")
        if doc.get("version") != API_VERSION:
            raise ContractError("unsupported robot API version")
        rid = doc.get("request_id")
        if not isinstance(rid, str) or not rid:
            raise ContractError("response requires request_id")
        action = doc.get("action")
        if not isinstance(action, dict):
            raise ContractError("request requires action")
        if action.get("action_type") not in ACTIONS:
            raise ContractError("action_type is invalid")
        if action.get("request_id") != rid:
            raise ContractError("action request_id does not match envelope")
        for k in ("source", "target", "metadata"):
            if action.get(k) is not None and not isinstance(action[k], dict):
                raise ContractError("source and target must be objects" if k != "metadata" else "metadata must be an object")
        return action

    def _execute(action: dict, rid: str) -> dict:
        kind = action["action_type"]
        with obs.span("adapter.action", kind, action_type=kind, object_id=action.get("object_id") or "",
                      request_id=rid, simulated=backend.simulated):
            if kind in ("NO_OP", "WAIT", "OBSERVE"):
                return result(rid, "success", f"{kind.lower()}: nothing to move")
            if kind != "POINT_AT_OBJECT":
                return result(rid, "failed", f"{kind} is not implemented on the robot yet; POINT_AT_OBJECT is")
            if not busy.acquire(blocking=False):
                return result(rid, "retryable", "another action is running; one at a time")
            try:
                if registration is None:
                    raise Refused("T_bb<-room has not been measured (GET /registration): refusing to convert a room "
                                  "pose into a place to drive to")
                with obs.span("adapter.transform", "room -> bb") as sp:
                    here = frames.bb_to_room((backend.pose_bb[0], backend.pose_bb[1], 0.0), registration.T)
                    plan = plan_point(action.get("target") or {}, registration, here[:2])
                    if sp is not None:
                        sp.set_data("stand_room", plan.stand_room)
                        sp.set_data("navigate", plan.navigate)
                with obs.span("adapter.navigate", "to the standoff", **{k: v for k, v in plan.navigate.items()}):
                    backend.navigate(plan.navigate["x"], plan.navigate["y"], plan.navigate["heading"])
                with obs.span("adapter.point", action.get("object_id") or ""):
                    backend.point_at(*plan.object_bb)
                tag = "SIMULATED: " if backend.simulated else ""
                return result(rid, "success", f"{tag}pointing at {action.get('object_id') or 'the target'} from "
                              f"{STANDOFF_M:.2f} m, room heading {plan.heading_room_deg:+.0f}°")
            except Refused as e:
                return result(rid, "failed", str(e))
            finally:
                busy.release()

    return app


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sim", action="store_true", help="simulated base and arm (the only mode that moves anything)")
    ap.add_argument("--host", default=os.getenv("ROBOT_ADAPTER_HOST", "127.0.0.1"),
                    help="localhost by default. Anything else REQUIRES a token and ROBOT_ALLOW")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    import uvicorn
    token = os.getenv("HOUSEBOT_ROBOT_TOKEN", "").strip() or None
    allow = tuple(x.strip() for x in os.getenv("ROBOT_ALLOW", "").split(",") if x.strip())
    if a.host not in ("127.0.0.1", "localhost", "::1") and not (token and allow):
        # the token crosses the network in clear text: on a shared wifi it is only as good as the allowlist
        print(f"refusing to listen on {a.host} without BOTH HOUSEBOT_ROBOT_TOKEN and ROBOT_ALLOW", file=sys.stderr)
        return 2
    if token is None:
        print("HOUSEBOT_ROBOT_TOKEN is unset: no bearer check. Fine on 127.0.0.1, not anywhere else.", file=sys.stderr)
    uvicorn.run(create_app(SimBackend() if a.sim else None, token=token, allow=allow), host=a.host, port=a.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
