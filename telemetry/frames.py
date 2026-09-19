"""telemetry/frames.py — the camera frame a robot-failure report attaches.

obs.robot_failure(frame=...) turns a failed grasp into a Sentry issue with a photograph of what
the arm was aiming at. The frame has to come from the capture the target pose was computed
from, so the capture assembler keeps one small JPEG per camera here and the hub reads it back
when a job fails:

    writer (whatever assembles a capture — docs/10 D9's pipeline):
        FRAMES.put(capture_id, "cam0", left_rect)          # once per camera per capture
    reader (telemetry/hub.py, on `job` failed):
        FRAMES.get(capture_id, camera)                     # bytes, or None

Stored already small (JPEG q70, longest side <= 640 px, via obs.small_jpeg) because the only
consumer is a Sentry attachment. Keeps the newest `keep` captures and deletes the rest.
Lives OUTSIDE the repo (FRAME_CACHE_DIR, default ~/.cache/gitspace/frames): photographs of a
room must never ride along into a commit.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_SAFE = re.compile(r"[^A-Za-z0-9_.-]")
PREFERRED = ("cam0", "cam1", "cam2")                 # cam0 faces forward: the arm's view


def _name(s: str) -> str:
    return _SAFE.sub("_", str(s))[:64] or "_"      # ids arrive off the wire: never a path


class FrameCache:
    def __init__(self, root: Path | str | None = None, keep: int = 50):
        self.root = Path(os.path.expanduser(str(root or os.getenv("FRAME_CACHE_DIR", "~/.cache/gitspace/frames"))))
        self.keep = keep

    def put(self, capture_id: str, camera: str, frame) -> Path | None:
        """frame: a BGR array (left_rect) or JPEG bytes. Returns the path, or None if it could
        not be encoded. Never raises: losing a failure photo must not lose a capture."""
        try:
            if str(ROOT) not in sys.path:
                sys.path.insert(0, str(ROOT))
            import obs
            jpeg = obs.small_jpeg(frame)
            if not jpeg:
                return None
            d = self.root / _name(capture_id)
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{_name(camera)}.jpg"
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(jpeg)
            tmp.replace(path)                         # a reader never sees half a file
            self._prune()
            return path
        except Exception:  # noqa: BLE001
            return None

    def get(self, capture_id: str | None, camera: str | None = None) -> bytes | None:
        """The frame for (capture_id, camera); camera None or missing -> cam0, cam1, cam2, any."""
        if not capture_id:
            return None
        d = self.root / _name(capture_id)
        if not d.is_dir():
            return None
        order = ([_name(camera)] if camera else []) + [c for c in PREFERRED if c != camera]
        for cam in order:
            f = d / f"{cam}.jpg"
            if f.is_file():
                return f.read_bytes()
        rest = sorted(d.glob("*.jpg"))
        return rest[0].read_bytes() if rest else None

    def _prune(self) -> None:
        dirs = sorted((p for p in self.root.iterdir() if p.is_dir()), key=lambda p: p.stat().st_mtime)
        for old in dirs[:-self.keep] if len(dirs) > self.keep else []:
            for f in old.glob("*"):
                f.unlink(missing_ok=True)
            old.rmdir()
