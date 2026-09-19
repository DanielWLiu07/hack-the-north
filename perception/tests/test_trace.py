"""The trace shows where the time goes: every stage of depth -> fuse -> voxelize -> costmap ->
serialize opens an obs.span, nested per camera, carrying the numbers that explain a capture.

Tested at the obs boundary with a recording fake: no sentry_sdk.init, nothing sent. The same
calls report for real the moment SENTRY_DSN is restored."""
import contextlib
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import depth  # noqa: E402  (puts the repo root on sys.path)
import costmap  # noqa: E402
import fuse  # noqa: E402
import obs  # noqa: E402
import serialize  # noqa: E402
import voxelize  # noqa: E402
import test_depth as td  # noqa: E402

CUBE = ((-4.0, -4.0, 0.0), 8.0, 7)


class _Span:
    def __init__(self, rec):
        self.rec = rec

    def set_data(self, k, v):
        self.rec["data"][k] = v


class Recorder:
    """Stands in for obs.span / obs.capture_quality and remembers what they were told."""

    def __init__(self, real_quality):
        self.spans, self.stack, self.real_quality = [], [], real_quality

    @contextlib.contextmanager
    def span(self, op, desc="", **data):
        rec = {"op": op, "data": dict(data), "parent": self.stack[-1]["op"] if self.stack else None}
        self.spans.append(rec)
        self.stack.append(rec)
        try:
            yield _Span(rec)
        finally:
            self.stack.pop()

    def capture_quality(self, skew_ms, tilt_rate_max, coverage):
        ok = self.real_quality(skew_ms, tilt_rate_max, coverage)      # obs's thresholds, not a copy
        self.stack[-1]["data"].update(skew_ms=skew_ms, tilt_rate_max=tilt_rate_max,
                                      coverage=coverage, quality_ok=ok)
        return ok


@pytest.fixture
def rec(monkeypatch):
    r = Recorder(obs.capture_quality)
    monkeypatch.setattr(obs, "span", r.span)
    monkeypatch.setattr(obs, "capture_quality", r.capture_quality)
    return r


def test_every_stage_is_a_span_with_its_numbers(rec, tmp_path):
    td._write_calib(tmp_path / "c.yaml")
    sbs = np.hstack(td._pair(td._texture(), 1000.0))
    out, ok = depth.depth_capture({"cam0": sbs}, {"cam0": depth.StereoDepth(str(tmp_path / "c.yaml"))},
                                  skew_ms=1.4, tilt_rate_max=0.031)
    xyz, valid, _ = out["cam0"]
    _, cloud = fuse.fuse([(xyz, valid, fuse.Mount(90, 1.0))])
    grid = voxelize.VoxelGrid.from_points(cloud, CUBE)
    costmap.Costmap.from_grid(grid)
    serialize.serialize(tmp_path / "room", [serialize.Measured(
        "mug_a1b2", "mug", "desk", (0.42, 0.18, 0.76), (0.12, 0.09, 0.11), 15.0, "#2b4c7e",
        "2026-09-18T21:00:00Z")], head={})

    by_op = {s["op"]: s for s in rec.spans}
    want = ["perception.stereo", "perception.rectify", "perception.sgbm", "perception.reproject",
            "perception.capture_gate", "perception.fuse", "perception.floor_check",
            "perception.voxelize", "perception.costmap", "perception.serialize"]
    assert set(want) <= set(by_op), sorted(by_op)
    for child in ("perception.rectify", "perception.sgbm", "perception.reproject"):
        assert by_op[child]["parent"] == "perception.stereo"             # grouped per camera
    assert by_op["perception.floor_check"]["parent"] == "perception.fuse"
    gate = by_op["perception.capture_gate"]["data"]
    assert ok and gate["quality_ok"] is True and gate["skew_ms"] == 1.4 and gate["tilt_rate_max"] == 0.031
    assert 0.6 < gate["coverage"] <= 1.0
    assert by_op["perception.stereo"]["data"]["camera"] == "cam0"
    assert abs(by_op["perception.floor_check"]["data"]["floor_z"]) < 0.05
    # the Mount check rides on the same span: a tilted floor is a pitch error (docs/10)
    fc = by_op["perception.floor_check"]["data"]
    assert {"floor_tilt_ahead_deg", "floor_tilt_side_deg", "floor_z_at_robot"} <= set(fc)
    assert by_op["perception.voxelize"]["data"]["n_voxels"] > 100
    assert by_op["perception.serialize"]["data"]["n_changed"] == 1


def test_nothing_initialised_sentry(rec):
    import sentry_sdk
    assert not sentry_sdk.get_client().is_active()
