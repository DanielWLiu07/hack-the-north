"""THE test. Scan an untouched room twice -> `git diff --exit-code` is clean.

If this is red the system is a liar and nothing else matters (tests/README.md, gate G2).
Treat red as stop-the-line.

Every check runs against every scanner that exists:
  fake        fake/scene_gen.py — sub-quantum jitter on every capture, objects sitting on
              1 cm bucket boundaries, a flaky object, occlusion. Always available.
  perception  recorded real captures replayed through the real pipeline. Skips until both
              exist: set GITSPACE_RECORDINGS to a directory of recordings, and provide
              perception.pipeline.scan_into(repo_dir, recording) that writes the working
              tree (via roomctl.state.write_tree) against the repo's HEAD.

"Clean" means clean the way git means it: nothing modified, nothing deleted, nothing
untracked. `git diff --exit-code` alone misses untracked files, so both are checked.
"""
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fake import scene_gen  # noqa: E402
from fake.scene_gen import FakeRoom, load_scene  # noqa: E402
from roomctl.repo import git_bin  # noqa: E402

RESCANS = 20
SCENES = ["clean_bench", "messy_bench", "movie_night", "bench_with_hammer"]


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([git_bin(), "-C", str(repo), *args], capture_output=True, text=True)


def dirt(repo: Path) -> str:
    """'' when the working tree is exactly HEAD. Otherwise git's own words for why not.
    `status --porcelain -uall` is empty iff `git diff --exit-code` is clean AND nothing is
    staged or untracked — the strict superset, in one git call."""
    status = git(repo, "status", "--porcelain", "-uall").stdout
    return status and status + git(repo, "diff").stdout


# ── scanners ─────────────────────────────────────────────────────────────────

class Fake:
    name = "fake"

    def __init__(self, repo: Path, seed: str):
        self.room = FakeRoom(repo, seed=seed, quiet=True)

    def commit(self, scene):
        return self.room.commit(scene, "baseline")

    def scan(self, scene):
        self.room.scan(scene)
        self.room.actions.clear()  # the ES docs aren't under test here


class Perception:
    name = "perception"

    def __init__(self, repo: Path, seed: str):
        recordings = os.getenv("GITSPACE_RECORDINGS")
        if not recordings:
            pytest.skip("no recorded captures: set GITSPACE_RECORDINGS")
        try:
            from perception.pipeline import scan_into
        except ImportError:
            pytest.skip("perception.pipeline.scan_into(repo_dir, recording) doesn't exist yet")
        self.repo, self.scan_into = repo, scan_into
        self.recordings = sorted(Path(recordings).iterdir())
        if len(self.recordings) < 2:
            pytest.skip(f"need at least 2 recordings of one untouched scene in {recordings}")
        self.rescans = len(self.recordings) - 1  # one scan per recording, checked after EACH
        self._next = 1

    def commit(self, scene):
        self.scan_into(self.repo, self.recordings[0])
        git(self.repo, "add", "-A")
        git(self.repo, "-c", "user.name=test", "-c", "user.email=test@local", "commit", "-qm", "baseline")

    def scan(self, scene):
        """ONE recording per call. Replaying them all and checking once would let a phantom
        that one scan adds and the next clears slip past the assert (docs/10 P18)."""
        self.scan_into(self.repo, self.recordings[self._next])
        self._next += 1


@pytest.fixture(params=[Fake, Perception], ids=lambda s: s.name)
def scanner(request, tmp_path):
    return lambda seed="gitspace": request.param(tmp_path / "room.git", seed)


# ── the gate ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("scene", SCENES)
@pytest.mark.parametrize("seed", ["gitspace", "second-seed"])
def test_rescanning_an_untouched_room_is_clean(scanner, scene, seed):
    s = scanner(seed)
    if isinstance(s, Perception) and (scene, seed) != (SCENES[0], "gitspace"):
        pytest.skip("perception replays the same recordings whatever the scene/seed: run once")
    s.commit(scene)
    repo = Path(s.room.repo) if isinstance(s, Fake) else s.repo
    assert dirt(repo) == "", "the baseline commit left the tree dirty"
    for i in range(getattr(s, "rescans", RESCANS)):
        s.scan(scene)
        assert dirt(repo) == "", f"PHANTOM DIFF on rescan {i + 1} of an untouched {scene}"


def test_occluded_object_is_carried_forward_not_deleted(tmp_path):
    """docs/20 Part 4: in HEAD, absent, occluder in the way -> unobserved, byte-identical."""
    room = FakeRoom(tmp_path / "room.git", quiet=True)
    room.commit("clean_bench")
    for _ in range(5):
        room.scan("occluded_bench")
        assert git(room.repo, "status", "--porcelain", "-uall").stdout.split() == \
            ["??", "zones/desk/laptop_8e4b.yaml"]


def test_moving_one_object_changes_exactly_one_file(tmp_path):
    """G2's second half: a real move is one modified file, nothing else."""
    room = FakeRoom(tmp_path / "room.git", quiet=True)
    base = load_scene("clean_bench")
    room.commit(base)
    book = base.objects["book_e5f6"]
    room.scan(replace(base, objects={**base.objects, "book_e5f6": replace(book, x=book.x + 0.20)}))
    assert git(room.repo, "status", "--porcelain", "-uall").stdout.split() == ["M", "zones/shelf/book_e5f6.yaml"]
    assert git(room.repo, "diff", "-U0").stdout.count("\n+  ") == 1  # one line: x


def test_a_nudge_inside_the_deadband_is_no_diff(tmp_path):
    """8 mm < 1.5 quanta: a real, tiny move that must not become a commit."""
    room = FakeRoom(tmp_path / "room.git", quiet=True)
    base = load_scene("clean_bench")
    room.commit(base)
    mug = base.objects["mug_a1b2"]
    for _ in range(10):
        room.scan(replace(base, objects={**base.objects, "mug_a1b2": replace(mug, x=mug.x + 0.008)}))
        assert dirt(room.repo) == ""


def test_the_gate_has_teeth(tmp_path, monkeypatch):
    """With BOTH deadbands gone — settle()'s object-level "didn't move" and stabilize()'s
    per-field hysteresis — quantization must flicker on the bucket-boundary objects. Otherwise
    the gate above would pass whether or not either does its job."""
    monkeypatch.setattr(scene_gen, "stabilize", lambda new, committed, q=0.01, hyst=0: round(new / q) * q)
    monkeypatch.setattr(scene_gen, "settle", lambda prev, measured: measured)
    room = FakeRoom(tmp_path / "room.git", quiet=True)
    room.commit("clean_bench")
    for _ in range(RESCANS):
        room.scan("clean_bench")
        if dirt(room.repo):
            return
    pytest.fail(f"{RESCANS} rescans without hysteresis stayed clean: the fake's jitter can't catch a regression")


def test_sentry_mode_stamps_one_real_trace_per_capture_and_files_the_rejection(tmp_path, monkeypatch):
    """--sentry, with obs stubbed at the boundary: nothing is sent. Every doc of a capture
    carries the ids obs hands out inside that capture's scope, and a gate rejection becomes
    ONE robot_failure issue tagged with its capture_id."""
    import contextlib
    import obs
    live = {"n": 0}

    @contextlib.contextmanager
    def scope(cap, *a, **k):
        live["n"] += 1
        yield {"sentry_trace_id": f"{live['n']:032x}", "sentry_span_id": "1" * 16,
               "sentry_url": f"https://org.sentry.io/performance/trace/{live['n']:032x}/"}
    failures, gates = [], []
    monkeypatch.setattr(obs, "transaction", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(obs, "span", lambda *a, **k: contextlib.nullcontext())
    monkeypatch.setattr(obs, "capture_scope", scope)
    monkeypatch.setattr(obs, "capture_quality", lambda *a: gates.append(a) or True)
    monkeypatch.setattr(obs, "robot_failure", lambda kind, detail, **k: failures.append((kind, k)))
    monkeypatch.setattr(scene_gen, "_sentry_live", lambda: True)
    room = FakeRoom(tmp_path / "room.git", quiet=True, sentry=True)
    room.commit("clean_bench", bump=True)          # first shutter knocked: rejected, then retried
    by_cap: dict[str, set] = {}
    for _, doc in room.actions:
        if doc.get("capture_id", "").startswith("cap_"):
            by_cap.setdefault(doc["capture_id"], set()).add(doc.get("sentry_url"))
    assert set(by_cap) == {"cap_0001", "cap_0002"}
    assert all(len(urls) == 1 and None not in urls for urls in by_cap.values()), by_cap
    assert [(k, kw["capture_id"]) for k, kw in failures] == [("capture_rejected", "cap_0001")]
    assert failures[0][1]["synthetic"] == "fake/scene_gen" and failures[0][1]["telemetry"]
    assert len(gates) == 2


def test_sentry_mode_refuses_without_a_dsn(monkeypatch, capsys):
    monkeypatch.setenv("SENTRY_DSN", "")
    assert scene_gen.main(["--commit", "clean_bench", "--sentry", "--es", "off"]) == 2
    assert "nothing was sent" in capsys.readouterr().err


def test_settle_alone_holds_an_untouched_room(tmp_path, monkeypatch):
    """The object-level rule is load-bearing by itself: strip the per-field hysteresis and an
    untouched room still rescans clean (docs/10 P15 — real single-view noise needs it)."""
    monkeypatch.setattr(scene_gen, "stabilize", lambda new, committed, q=0.01, hyst=0: round(new / q) * q)
    room = FakeRoom(tmp_path / "room.git", quiet=True)
    room.commit("clean_bench")
    for _ in range(RESCANS):
        room.scan("clean_bench")
        assert dirt(room.repo) == ""


def test_fake_telemetry_has_the_drive_and_never_repeats_a_sample(tmp_path):
    """web /replay (docs/29): 8 s before the shutter to 2 s after, eight signals, encoders in
    cumulative turns that integrate the drive. A TSDS keeps one doc per (signal, @timestamp),
    so the rejected capture and its retry 5 s later must never emit the same sample twice."""
    from collections import Counter
    from datetime import datetime
    room = FakeRoom(tmp_path / "room.git", quiet=True)
    room.commit("clean_bench", bump=True)
    tel = [d for a, d in room.actions if next(iter(a.values()))["_index"] == "robot-telemetry"]
    assert {d["signal"] for d in tel} == {"pitch", "tilt_rate", "odom_residual", "left_enc", "right_enc",
                                          "motor_current_l", "motor_current_r", "balanced"}
    ids = Counter((d["signal"], d["@timestamp"]) for d in tel)
    assert max(ids.values()) == 1, "a (signal, @timestamp) pair was emitted twice"
    by_cap = {}
    for d in tel:
        by_cap.setdefault(d["sentry_trace_id"], []).append(d)
    first, retry = sorted(by_cap.values(), key=lambda ds: min(x["@timestamp"] for x in ds))
    t = lambda d: datetime.fromisoformat(d["@timestamp"].replace("Z", "+00:00"))  # noqa: E731
    assert (max(map(t, first)) - min(map(t, first))).total_seconds() == pytest.approx(10.0)
    assert min(map(t, retry)) > max(map(t, first))
    left = [d["value"] for d in sorted(first, key=t) if d["signal"] == "left_enc"]
    turns = abs(left[-1] - left[0])
    assert 0.40 * 0.85 / 0.518 < turns < 0.40 * 1.15 / 0.518, "0.4 m of arc, in 0.518 m turns"
    assert all(d["value"] == 1 for d in tel if d["signal"] == "balanced")
