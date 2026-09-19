"""The contract's rules, as code — docs/31. Andrew's side: awzheng/gitirl @ 35f4c96."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

HIS_VERBS = ("add", "commit", "status", "diff", "restore", "log")      # his CommandType, all six
HIS_TYPES_OUT = ("parsed_command", "robot_action", "command_status", "command_result", "error")
FRAME = "world_z_up"         # metres · X forward · Y left · Z UP · floor z=0 (docs/20)

# Graph-native verbs are OURS: never sent to his middleware, whatever the text around them says.
_GRAPH = re.compile(r"^(?:(?:room|git|gitirl)\s+)?(revert|merge|cherry[- ]?pick|branch|checkout|reset|"
                    r"rebase|stash|resolve)\b(?:\s+(?:--hard\s+)?(?P<ref>[\w./~^-]+))?", re.I)


class ContractError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, details: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.details = code, message, status, details or {}


# Outages are HTTP errors. Everything a person can cause by TYPING is a conversation: HTTP 200,
# `ok: false`, a structured error — a judge's "put the mug back on the shelf" must render as
# "I didn't understand", never as a broken page.
INFRA = {"bridge_unavailable", "room_unavailable", "frame_mismatch", "intent_unavailable"}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def envelope(type_: str, request_id: str, payload: dict) -> dict:
    """One of HIS message types, in his envelope. We never invent a type value."""
    assert type_ in HIS_TYPES_OUT, f"not a gitirl-agent message type: {type_!r}"
    return {"type": type_, "request_id": request_id, "timestamp": now_iso(), "payload": payload}


def read_request(body) -> tuple[str, str]:
    """(request_id, text) from the panel's envelope, or ContractError(bad_request)."""
    if not isinstance(body, dict) or body.get("type") != "user_command":
        raise ContractError("bad_request", 'the envelope\'s "type" must be "user_command"')
    rid, payload = body.get("request_id"), body.get("payload")
    if not isinstance(rid, str) or not rid.strip() or len(rid) > 128:
        raise ContractError("bad_request", "request_id must be a non-empty string (the panel makes it)")
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str) or not payload["text"].strip():
        raise ContractError("bad_request", 'payload.text must be the command text')
    return rid.strip(), payload["text"].strip()[:500]


_CLI_PREFIX = re.compile(r"^(?:room|git)\s+", re.I)


def for_parser(text: str) -> str:
    """What his parser sees: `room status` / `git log` typed CLI-style lose the prefix (his grammar
    strips only `gitirl `). raw_text keeps what the person actually typed."""
    return _CLI_PREFIX.sub("", " ".join(text.split()), count=1)


def route(text: str) -> tuple[str, str | None, str | None]:
    """("graph", verb, ref) for a graph-native command, else ("middleware", None, None)."""
    m = _GRAPH.match(" ".join(text.split()))
    if not m:
        return "middleware", None, None
    verb = re.sub(r"[ ]", "-", m.group(1).lower()).replace("cherrypick", "cherry-pick")
    return "graph", verb, m.group("ref")


def check_intent(text: str, intent: dict) -> None:
    """His parser deciphered `text` into `intent`. Refuse the one confusion that moves the robot the
    wrong way: `restore` coming back for text that asked to revert (docs/31 §1)."""
    if intent.get("command") == "restore" and re.search(r"\brevert", text, re.I):
        raise ContractError("intent_mismatch", "the text asks to REVERT (undo a commit) but the bridge "
                            "answered RESTORE (match a state) — not the same operation; refused", 422)
    if intent.get("command") not in HIS_VERBS:
        raise ContractError("unknown_command", "not one of gitirl-agent's six verbs", 422)


def _declared(d: dict):
    """The frame a dict declares for everything inside it: its own `frame`, or `metadata.frame`
    (ANDREW-HANDOFF.md §2 row 6: robot_observation carries it under observation.metadata)."""
    if "frame" in d:
        return d["frame"]
    meta = d.get("metadata")
    return meta.get("frame") if isinstance(meta, dict) and "frame" in meta else None


def _poses(x, path="payload", frame=None):
    """Yield (path, declared frame) for every pose-like dict (numeric x, y, z) inside x; the frame
    is the nearest one declared on it or on an enclosing dict."""
    if isinstance(x, dict):
        frame = _declared(x) or frame
        if all(isinstance(x.get(k), (int, float)) for k in ("x", "y", "z")):
            yield path, frame
        for k, v in x.items():
            yield from _poses(v, f"{path}.{k}", frame)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _poses(v, f"{path}[{i}]", frame)


def assert_frame(payload: dict, direction: str) -> None:
    """Every pose crossing to or from his side is in OUR frame, declared. Never converted here:
    Bracket Bot is Y-down and that conversion lives in his RobotAdapter (docs/31 §4)."""
    bad = [(p, f) for p, f in _poses(payload) if f != FRAME]
    if bad:
        (path, declared), n = bad[0], len(bad)
        raise ContractError("frame_mismatch", f"{direction}: {n} pose(s) (first at {path}) declare "
                            f"frame {declared!r}, expected {FRAME!r} — refusing rather than converting "
                            f"(the Y-down conversion belongs to his RobotAdapter)", 422)


def new_request_id() -> str:
    return uuid.uuid4().hex
