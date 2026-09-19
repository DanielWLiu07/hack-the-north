"""The judge's click, end to end, against LIVE services: a fresh capture goes through the real
pipeline with Sentry on, lands in the live cluster, and the running web server's /capture/<id>
and /api/replay/<id> serve it -- every block checked against what the pipeline produced.

The input frames are SYNTHETIC (perception/synthetic.py: no camera here); the depth, the
cloud, the objects, the Sentry trace and the documents are all real. The capture's documents
and its .ply are deleted afterwards. Opt-in (it spends quota):

    GITSPACE_LIVE=1 python3 -m pytest perception/tests/test_capture_live.py
"""
import base64
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import es_sink  # noqa: E402
import pipeline  # noqa: E402  (puts the repo root on sys.path)
import synthetic  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
ENV = {**dotenv_values(REPO / ".env"), **os.environ}
WEB = os.getenv("GITSPACE_WEB", "http://127.0.0.1:8000")


def _up() -> bool:
    try:
        return urllib.request.urlopen(f"{WEB}/api/status", timeout=3).status == 200
    except OSError:
        return False


LIVE = (os.getenv("GITSPACE_LIVE") == "1" and bool(es_sink._usable(ENV.get("ELASTIC_API_KEY")))
        and bool(es_sink._usable(ENV.get("ELASTIC_URL"))) and _up())
pytestmark = pytest.mark.skipif(not LIVE, reason="live: GITSPACE_LIVE=1, usable ELASTIC_* keys, and the web "
                                                 "server answering on " + WEB)


class RestES:
    """The two calls this test needs, over plain HTTPS -- the elasticsearch client isn't
    installed beside sklearn in any one interpreter here. es_sink drives it like the real one."""
    def __init__(self):
        self.url, self.auth = ENV["ELASTIC_URL"].rstrip("/"), f"ApiKey {ENV['ELASTIC_API_KEY']}"

    def _call(self, method, path, body: bytes | None, ctype="application/json"):
        req = urllib.request.Request(self.url + path, data=body, method=method,
                                     headers={"Authorization": self.auth, "Content-Type": ctype})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def bulk(self, operations, refresh=None):
        nd = "".join(json.dumps(o) + "\n" for o in operations).encode()
        return self._call("POST", f"/_bulk?refresh={refresh or 'false'}", nd, "application/x-ndjson")

    def search(self, index, size, query, sort, search_after=None):
        body = {"size": size, "query": query, "sort": sort, **({"search_after": search_after} if search_after else {})}
        return self._call("POST", f"/{index}/_search", json.dumps(body).encode())

    def delete_capture(self, capture_id):
        q = json.dumps({"query": {"term": {"capture_id": capture_id}}}).encode()
        for index in ("room-clouds", "room-observations"):
            self._call("POST", f"/{index}/_delete_by_query?refresh=true", q)


def _get(path):
    with urllib.request.urlopen(WEB + path, timeout=30) as r:
        return r.status, r.read()


@pytest.fixture(scope="module")
def captured(tmp_path_factory):
    import obs
    live_sentry = obs.init("laptop")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    rec = synthetic.write_recordings(tmp_path_factory.mktemp("rec"), 1, seed=int(now.timestamp()) % 97, start=now)[0]
    meta = pipeline.load_recording(rec)
    es = RestES()
    try:
        result = pipeline.scan_into(tmp_path_factory.mktemp("room") / "room", rec, es=es)
        obs.flush(10)
        yield dict(meta=meta, result=result, sentry=live_sentry)
    finally:
        es.delete_capture(meta.capture_id)
        (pipeline.CLOUDS / f"{meta.capture_id}.ply").unlink(missing_ok=True)


def test_the_capture_page_renders(captured):
    status, html = _get(f"/capture/{captured['meta'].capture_id}")
    assert status == 200 and b"<html" in html.lower()


def test_every_block_is_this_pipeline_run(captured):
    meta, result = captured["meta"], captured["result"]
    status, body = _get(f"/api/capture/{meta.capture_id}")
    d = json.loads(body)
    assert status == 200 and d["source"] == "elasticsearch" and d["synthetic"] is False
    # the quality gate: the recording's numbers, judged PASS
    assert d["gate"]["values"]["skew_ms"] == meta.skew_ms and d["gate"]["values"]["tilt_rate_max"] == meta.tilt_rate_max
    assert d["gate"]["pass"] is True and 0.6 < d["gate"]["values"]["coverage"] <= 1.0
    # the objects: exactly the ones the scan wrote, each with its own camera's raw position
    assert len(d["objects"]) == result.objects == 3
    for o in d["objects"]:
        [cam] = o["cameras"]
        assert cam["camera"] == "fused" and 0.0 < cam["raw_x"] < 1.1 and 0.68 < cam["raw_z"] < 1.0
    # the point cloud: the catalog's count is the .ply on disk
    ply = pipeline.CLOUDS / f"{meta.capture_id}.ply"
    assert f"element vertex {d['point_count']}\n".encode() in ply.read_bytes()[:200]
    # Sentry: this scan's own trace, linked
    assert re.fullmatch(r"[0-9a-f]{32}", d["sentry"]["trace_id"])
    if captured["sentry"]:
        assert d["sentry"]["url"] and d["sentry"]["url"].endswith(f"/trace/{d['sentry']['trace_id']}/")


def test_replay_reports_what_telemetry_exists_and_invents_none(captured):
    """No robot streamed telemetry for a synthetic capture: the replay must say so, not draw a line.
    Where samples DO exist they must be the raw 50 Hz series (20 ms apart), not buckets."""
    status, body = _get(f"/api/replay/{captured['meta'].capture_id}")
    d = json.loads(body)
    assert status == 200 and d["capture_id"] == captured["meta"].capture_id
    for name, series in d["signals"].items():
        ts = [p[0] for p in series]
        assert len(ts) == d["recorded"][name]
        if len(ts) > 2:
            assert all(abs((b - a) - 0.02) < 1e-6 for a, b in zip(ts, ts[1:])), name
    if not any(d["recorded"].values()):
        assert d["path"] is None


# ── the rest of the judge's path, against what is live now ───────────────────

def test_traversal_runs_on_live_room_git_and_room_voxels():
    """docs/24 A: HEAD's objects from the live room.git, its occupancy from the live room-voxels
    (docs/24 A5), through costmap -> solve_base_pose_why. Every object gets a stance or the
    filter tally that says why not -- never a silent None."""
    import math
    import costmap
    import voxelize
    from roomctl.executor import ARM, HOME, ROBOT
    from roomctl.repo import Repo
    repo = Repo(REPO / "room.git")
    records, head = repo.records(), repo.head()
    docs = voxelize.voxels_for_commit(RestES(), head)
    assert records and docs, "live room.git HEAD has no objects or no indexed voxels"
    grid = voxelize.VoxelGrid.from_docs(docs)
    assert len(grid.ijk) == len(docs) == len({d["voxel_key"] for d in docs})
    cm = costmap.Costmap.from_grid(grid)
    for oid, rec in records.items():
        target = (rec.pose.x, rec.pose.y, rec.pose.z)
        ignore = math.hypot(rec.extents.x, rec.extents.y, rec.extents.z) / 2 + grid.leaf
        pose, why = costmap.solve_base_pose_why(target, cm, ARM, (HOME.x, HOME.y, math.radians(HOME.yaw)),
                                                ROBOT.eye_h, ignore)
        if pose is None:
            assert sum(why.values()) == 180, (oid, why)
        else:
            r = math.hypot(target[0] - pose[0], target[1] - pose[1])
            assert ARM.r_min - 1e-6 <= r <= ARM.r_max + 1e-6 and not cm.occupied(pose[0], pose[1]), oid


def test_history_graph_is_the_live_room_git():
    """docs/24 B: the graph the page draws IS `git log` of room.git -- same commits, same order."""
    import subprocess
    status, body = _get("/api/graph")
    nodes = json.loads(body)["nodes"]
    log = subprocess.run(["git", "-C", str(REPO / "room.git"), "log", "--all", "--format=%h %s"],
                         capture_output=True, text=True, check=True).stdout.split("\n")
    shas = [line.split(" ", 1)[0] for line in log if line]
    got = [(n.get("sha") or n.get("commit_sha") or n.get("id"))[:len(shas[0])] for n in nodes]
    assert status == 200 and sorted(got) == sorted(shas)


def test_replay_is_the_raw_50hz_series_for_every_capture():
    """/api/replay/<id> for every capture in room-clouds, signal by signal, against the raw
    robot-telemetry documents in the same window: the SAME samples at the same instants -- none
    dropped, none bucketed, none invented -- and 20 ms apart within every contiguous run.
    Holes are the source's, not the replay's: today's telemetry is synthetic and exists only
    +-2 s around fake shutters (cap_0005's window spans its own and cap_0004's, 1 s apart)."""
    from datetime import datetime
    es = RestES()
    hits = es._call("POST", "/room-clouds/_search",
                    json.dumps({"size": 20, "_source": ["capture_id"]}).encode())["hits"]["hits"]
    ids = [h["_source"]["capture_id"] for h in hits]
    assert ids
    compared = 0
    for cid in ids:
        status, body = _get(f"/api/replay/{cid}")
        d = json.loads(body)
        assert status == 200 and not d.get("downsampled"), cid
        t0 = datetime.fromisoformat(d["t0_ts"].replace("Z", "+00:00"))
        for name, series in d["signals"].items():
            q = {"size": 10000, "sort": [{"@timestamp": "asc"}], "_source": ["@timestamp"],
                 "query": {"bool": {"filter": [{"term": {"signal": name}}, {"range": {"@timestamp": {
                     "gte": d["window"]["from"], "lte": d["window"]["to"]}}}]}}}
            raw = [round((datetime.fromisoformat(h["_source"]["@timestamp"].replace("Z", "+00:00")) - t0)
                         .total_seconds(), 3)
                   for h in es._call("POST", "/robot-telemetry/_search", json.dumps(q).encode())["hits"]["hits"]]
            got = [round(p[0], 3) for p in series]
            assert got == raw, (cid, name, len(got), len(raw))
            runs = [round(b - a, 3) for a, b in zip(got, got[1:]) if b - a < 0.5]      # inside a run
            assert all(g == 0.02 for g in runs), (cid, name, sorted(set(runs))[:5])
            compared += len(got) > 2
    assert compared, "no capture had any telemetry to replay"
