"""scripts/seer_sweep.py — which issues Seer is bought for, what the caps refuse, and how a finding reads.
No network: every test here is the script's own judgement, not Sentry's."""
import importlib.util
import sys
import time
from pathlib import Path

import pytest

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root), str(root / "scripts")]
spec = importlib.util.spec_from_file_location("seer_sweep", root / "scripts/seer_sweep.py")
sw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sw)

CFG = {"skip": set()}


def issue(title, level="error", **more):
    return {"id": "7743074453", "short_id": "GITSPACE-19", "title": title, "level": level,
            "count": 1, "permalink": "https://sentry.io/x", "culprit": "c", **more}


@pytest.mark.parametrize("title,why", [
    ("robot: robot_unreachable — nothing answers at 192.168.68.63 (timed out)", "a link or power state"),
    ("robot: robot_server_down — :8080 does not answer", "a link or power state"),
    ("robot: camera_unavailable_recovered — cleared after 480 s", "a link or power state"),
    ("robot: robot_restarted — boot_id 38215 -> 091878", "a link or power state"),
    ("ModuleNotFoundError: No module named 'elasticsearch'", "deploy noise"),
    ("[Errno 98] error while attempting to bind on address ('0.0.0.0', 8080): address already in use", "deploy noise"),
])
def test_the_noise_is_left_alone(title, why):
    """A debugging agent reading our source has nothing to say about a robot somebody unplugged, or a
    server started from the wrong directory. Every one of these is a real title from the live project."""
    assert why in sw.skip_reason(issue(title), CFG)


@pytest.mark.parametrize("title", [
    "ValueError: '192.168.0.30   # the laptop' does not appear to be an IPv4 or IPv6 network",
    "robot: fell_over — balanced went 0; pitch 0.350199, peak tilt_rate 0.698854",
    "robot: object_not_found — nothing in the room answers 'pick up the trash' (best 1.029 < 1.05)",
    "robot: camera_unavailable — /dev/v4l/by-path/x did not open",
    "KeyError: 'detail'",
])
def test_a_defect_is_worth_buying_a_run_for(title):
    """`camera_unavailable` is a device that did not open, not a cable: it stays in. The three-band
    resolver refusing a real sentence stays in too — that one is our logic, and Seer can read it."""
    assert sw.skip_reason(issue(title), CFG) is None


def test_a_notice_is_not_a_defect_and_the_skip_list_is_obeyed():
    assert "info level" in sw.skip_reason(issue("gitspace sentry smoketest", level="info"), CFG)
    assert sw.skip_reason(issue("KeyError: 'detail'"), {"skip": {"GITSPACE-19"}}) == "on the skip list"
    assert "no usable issue id" in sw.skip_reason(issue("KeyError: 'x'", id="not-digits"), CFG)


def test_a_cap_that_cannot_be_read_is_not_a_cap(monkeypatch):
    monkeypatch.setenv("SEER_SWEEP_PER_HOUR", "lots")
    with pytest.raises(SystemExit, match="not a whole number"):
        sw.caps()
    monkeypatch.setenv("SEER_SWEEP_PER_HOUR", "999")
    with pytest.raises(SystemExit, match="outside 0..40"):
        sw.caps()


@pytest.mark.parametrize("stop", ["code_changes", "open_pr"])
def test_it_refuses_to_start_where_seer_would_write_code(monkeypatch, stop):
    """The sweep runs unattended. A stopping point past a solution lets Seer produce changes nobody is
    watching, so the refusal is in the code and names the value — not a comment asking politely."""
    monkeypatch.setenv("SEER_SWEEP_STOPPING_POINT", stop)
    with pytest.raises(SystemExit, match="would let Seer write code"):
        sw.caps()
    monkeypatch.setenv("SEER_SWEEP_STOPPING_POINT", "root_cause")
    assert sw.caps()["stop_at"] == "root_cause"


def test_the_hour_is_a_rolling_hour(monkeypatch, tmp_path):
    monkeypatch.setattr(sw, "STATE", tmp_path)
    rows = [{"t": time.time() - 4000}, {"t": time.time() - 100}, {"t": time.time()}]
    assert sw._spent(rows) == 2, "a run bought 66 minutes ago is not this hour's spend"
    kept = sw._record("GITSPACE-1", 42, rows)
    assert sw._spent(kept) == 3 and (tmp_path / "state.json").exists()
    assert sw._spent(sw._ledger()) == 3, "restarting the script must not restart the budget"


@pytest.mark.parametrize("said", [
    "Both fixes are already in the codebase (the `parse()` function in `robot/allow.py` strips comments).",
    "**Good news:** This was already fixed in commit `eb01a551`, pushed 52 minutes after the error.",
    "The mount constant was fixed by commit 49a10adb.",
    "This no longer applies: the branch was deleted.",
])
def test_seer_saying_it_is_already_fixed_is_caught(said):
    """Both phrasings came out of real runs. Missing one means a person edits a file that is already
    correct, instead of resolving the issue in Sentry."""
    assert sw.ALREADY_FIXED.search(said)


def test_an_ordinary_root_cause_is_not_mistaken_for_a_fix():
    assert not sw.ALREADY_FIXED.search(
        "The old PeerAllowList.__init__ did no comment stripping, so ip_network() raised ValueError.")


def test_the_already_fixed_banner_stays_one_blockquote(tmp_path):
    """A character window around the match ran across a paragraph break, and a blank line ENDS a markdown
    blockquote — the banner fell out of its own quote and read like the finding itself."""
    text = ("**Root cause identified.**\n\nThe middleware was built lazily, so it crashed on every "
            "request.\n\nBoth fixes are already in the codebase (`robot/allow.py` strips comments) — they "
            "were not deployed yet.\n")
    cfg = {"dir": tmp_path}
    path = sw.write_finding(cfg, issue("ValueError: bad network"), {"run_id": 1, "status": "COMPLETED"},
                            text, "root_cause", False, {})
    banner = [l for l in path.read_text().splitlines() if l.startswith(">")]
    assert any("ALREADY FIXED" in l for l in banner)
    quoted = "\n".join(banner)
    assert "already in the codebase" in quoted and "\n\n" not in quoted
    assert "Root cause identified" not in quoted, "the banner quotes the sentence, not the whole finding"


def test_a_file_named_twice_is_named_once():
    files, shas = sw._named("In `perception/pipeline.py` the call fails; pipeline.py has no `log`. "
                            "Fixed in eb01a551.")
    assert files == ["perception/pipeline.py"] and shas == ["eb01a551"]


def test_the_index_line_is_the_finding_not_its_heading():
    """Seer opens with a label. An index full of "Root cause identified." tells a reader nothing."""
    got = sw._excerpt("**Root cause identified.**\n\nThe head stereo camera is physically off the USB bus.")
    assert got == "The head stereo camera is physically off the USB bus."
    # long enough to pass a length test, and still only a lead-in
    lead_in = sw._excerpt("Here's the root cause analysis for GITSPACE-6:\n\nThe robot falls because the "
                          "balance controller loses effectiveness under low voltage.")
    assert lead_in.startswith("The robot falls because")
    fenced = sw._excerpt("## Root Cause\n\nThe crash is in `FakeReader.ready()` line 269:\n\n"
                         "```python\nn = world.n - (world.skew if self.name == 'camera.depth' else 0)\n```\n\n"
                         "The reader polls before the first tick, so world.n is still zero.")
    assert not fenced.startswith("n = world.n"), "an index row is prose about the bug, not a line of it"
    assert "polls before the first tick" in fenced


def test_nothing_in_the_script_asks_for_code():
    """The guarantee the directory's README makes, checked against the source rather than trusted."""
    src = (root / "scripts/seer_sweep.py").read_text()
    assert '"stopping_point": cfg["stop_at"]' in src
    assert src.count('client._request("POST"') == 1, "one POST in the whole script: the run that is bought"
    for forbidden in ("code_changes", "open_pr"):
        assert forbidden in sw.FORBIDDEN_STOPS


class StuckSeer:
    """A run that is bought and then never finishes — the case that lost the budget entry."""

    def __init__(self):
        self.posts = 0

    def state(self):
        return {"org": "na-alh"}

    async def _get(self, path, params=None):
        return {"autofix": None if self.posts == 0 else {"run_id": 99, "status": "PROCESSING"}}

    async def seer_setup(self, issue_id):
        return {"blocker": None, "caveat": None}

    async def _request(self, method, path, *, body=None, params=None):
        assert method == "POST" and body["stopping_point"] == "root_cause"
        self.posts += 1
        return {"run_id": 99}


def test_the_run_is_on_the_ledger_before_we_wait_for_it(tmp_path):
    """Charging after the poll meant that stopping the process mid-run lost the entry: the run was
    bought, Sentry billed it, and the next start read a budget that had forgotten it."""
    import asyncio
    charged = []
    cfg = {"stop_at": "root_cause", "poll_s": 0.01, "max_wait_s": 0.02, "dir": tmp_path}
    out = asyncio.run(sw.look_at(StuckSeer(), issue("KeyError: 'detail'"), cfg, may_start=True, write=True,
                                 budget_line="run 1 of 6", charge=charged.append))
    assert charged == [99], "the POST returned, so the run is spent — record it there, not after the wait"
    assert out["outcome"] == "slow" and out["paid"] is True
    assert not list(tmp_path.glob("*.md")), "an unfinished run is not a finding"


def test_the_skip_file_is_re_read_so_a_person_can_retire_an_issue_mid_loop(tmp_path, monkeypatch):
    """Several issues share one cause — the head camera off the USB bus explains five of them — and the
    loop runs unattended for hours. Editing a file beats stopping it to change an env var."""
    monkeypatch.setattr(sw, "STATE", tmp_path)
    assert sw.skip_file() == set()
    (tmp_path / "skip.txt").write_text("# a comment line\nGITSPACE-T   # same fault, another traceback\n"
                                       "\ngitspace-v\n")
    assert sw.skip_file() == {"GITSPACE-T", "GITSPACE-V"}
    assert sw.skip_reason(issue("bbos _read_frame failed", short_id="GITSPACE-T"),
                          {"skip": sw.skip_file()}) == "on the skip list"
