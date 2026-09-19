"""obs.py against venue wifi: keep_alive + a deep transport queue; every robot_failure ALSO a room-events
document (spooled first, _create/<event id>, idempotent); traces handed to worker threads and to the edge.
Runs obs.init() in a SUBPROCESS against loopback fakes of Sentry ingest, Elasticsearch and the edge:
nothing leaves 127.0.0.1, and this test process's own Sentry state is never touched."""
from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAPPING = json.loads((ROOT / "elastic" / "mappings" / "room-events.json").read_text())["template"]["mappings"]["properties"]


class Fake:
    """A loopback HTTP server that records every request; `answer(method, path)` -> (code, body)."""

    def __init__(self, answer=lambda m, p: (200, {})):
        self.got, fake = [], self

        class H(BaseHTTPRequestHandler):
            def _any(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                fake.got.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": raw})
                code, body = answer(self.command, self.path)
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            do_GET = do_POST = do_PUT = _any

            def log_message(self, *a):
                return
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


CHILD = textwrap.dedent('''
    import json, os, sys, threading, urllib.request
    sys.path.insert(0, os.environ["ROOT"])
    import obs, sentry_sdk
    out = {}
    out["init"] = obs.init("laptop")
    o = sentry_sdk.get_client().options
    out["keep_alive"], out["queue"] = o.get("keep_alive"), o.get("transport_queue_size")
    with obs.transaction("test.parent", "parent") as tx:
        out["tx_trace"] = tx.trace_id
        out["event_id"] = obs.robot_failure("nav_failed", "no path to the desk", zone="desk", object_id="mug_a1b2",
                                            capture_id="cap_0005", commit_sha="1a668ec")
        headers = obs.trace_headers()
        seen = {}
        def worker():
            with obs.transaction("test.child", "child", parent=headers) as t:
                seen["trace"] = t.trace_id
        th = threading.Thread(target=worker); th.start(); th.join()
        out["child_trace"] = seen["trace"]
        with obs.span("housebot.post", "POST /v1/jobs"):
            urllib.request.urlopen(urllib.request.Request(os.environ["EDGE"] + "/v1/jobs", data=b"{}", method="POST",
                                                          headers={"Content-Type": "application/json"}), timeout=5).read()
    for t in threading.enumerate():                     # the background shipper started by robot_failure
        if t.name == "obs-failures":
            t.join(10)
    out["ship"] = obs.ship_failures()
    out["spooled"] = sorted(p.name for p in obs._failure_spool().glob("*.json"))
    obs.flush(5)
    print(json.dumps(out))
''')


def run_child(tmp_path, es_url: str, edge_url: str, sentry_port: int) -> dict:
    env = {"ROOT": str(ROOT), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path),
           "SENTRY_DSN": f"http://publickey@127.0.0.1:{sentry_port}/1", "SENTRY_TRACES_SAMPLE_RATE": "1.0",
           "SENTRY_PROFILES_SAMPLE_RATE": "0", "ELASTIC_URL": es_url, "ELASTIC_API_KEY": "test-key",
           "ROBOT_FAILURE_SPOOL": str(tmp_path / "spool"), "EDGE": edge_url}
    r = subprocess.run([sys.executable, "-c", CHILD], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture()
def fakes():
    sentry, edge = Fake(), Fake()
    stored: dict[str, bytes] = {}

    def es_answer(method, path):
        doc_id = path.rsplit("/", 1)[-1]
        if doc_id in stored:
            return 409, {"error": {"type": "version_conflict_engine_exception"}}
        stored[doc_id] = b"x"
        return 201, {"result": "created"}
    es = Fake(es_answer)
    es.stored = stored
    yield sentry, es, edge
    for f in (sentry, es, edge):
        f.close()


def test_a_robot_failure_survives_as_a_room_events_document(tmp_path, fakes):
    sentry, es, edge = fakes
    out = run_child(tmp_path, f"http://127.0.0.1:{es.port}", f"http://127.0.0.1:{edge.port}", sentry.port)
    assert out["init"] is True and out["keep_alive"] is True and out["queue"] == 1000
    (put,) = [g for g in es.got if g["method"] == "PUT"]
    assert put["path"] == f"/room-events/_create/{out['event_id']}", "the Sentry event id is the doc id: idempotent"
    assert put["headers"]["Authorization"] == "ApiKey test-key"
    doc = json.loads(put["body"])
    assert set(doc) <= set(MAPPING), f"strict mapping: {set(doc) - set(MAPPING)}"
    assert (doc["event_type"], doc["outcome"], doc["author"], doc["zone"]) == ("robot_failure", "error", "laptop", "desk")
    assert doc["objects_affected"] == ["mug_a1b2"] and "capture_id" not in doc and "commit_sha" not in doc
    assert "cap_0005" in doc["message"] and "1a668ec" in doc["message"] and doc["message"].startswith("nav_failed: no path")
    assert doc["sentry_trace_id"] == out["tx_trace"], "joins to the waterfall it happened in"
    assert out["spooled"] == [], "shipped: the spool is empty"
    assert any(g["path"].startswith("/api/1/envelope") for g in sentry.got), "and Sentry got the event too"


def test_the_trace_goes_to_the_worker_and_to_the_edge(tmp_path, fakes):
    sentry, es, edge = fakes
    out = run_child(tmp_path, f"http://127.0.0.1:{es.port}", f"http://127.0.0.1:{edge.port}", sentry.port)
    assert out["child_trace"] == out["tx_trace"], "a worker's transaction continues the asker's trace"
    (post,) = edge.got
    h = {k.lower(): v for k, v in post["headers"].items()}
    assert h["sentry-trace"].split("-")[0] == out["tx_trace"] and "sentry-trace_id=" in h.get("baggage", "")


def test_elasticsearch_down_keeps_the_file_and_a_later_ship_sends_it_once(tmp_path, fakes):
    sentry, es, edge = fakes
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    out = run_child(tmp_path, f"http://127.0.0.1:{dead}", f"http://127.0.0.1:{edge.port}", sentry.port)
    assert out["spooled"] == [f"{out['event_id']}.json"] and out["ship"]["kept"] == 1, "nothing lost while ES is down"
    spool = tmp_path / "spool"
    ship = textwrap.dedent('''
        import json, os, sys
        sys.path.insert(0, os.environ["ROOT"])
        import obs
        print(json.dumps([obs.ship_failures(), obs.ship_failures()]))
    ''')
    env = {"ROOT": str(ROOT), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "ELASTIC_URL": f"http://127.0.0.1:{es.port}",
           "ELASTIC_API_KEY": "k", "ROBOT_FAILURE_SPOOL": str(spool)}
    first, second = json.loads(subprocess.run([sys.executable, "-c", ship], env=env, capture_output=True, text=True,
                                              timeout=60, check=True).stdout)
    assert first["sent"] == 1 and second == {"sent": 0, "already": 0, "kept": 0} and list(spool.glob("*.json")) == []
    (spool / f"{out['event_id']}.json").write_text("{}")                   # a second process sends it again: a 409
    again = json.loads(subprocess.run([sys.executable, "-c", ship], env=env, capture_output=True, text=True,
                                      timeout=60, check=True).stdout)[0]
    assert again["already"] == 1 and list(spool.glob("*.json")) == [], "already there is delivered, not an error"


def test_an_uninitialised_process_writes_nothing(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT))
    import obs
    monkeypatch.setenv("ROBOT_FAILURE_SPOOL", str(tmp_path / "spool"))
    monkeypatch.setattr(obs, "_ROLE", None)
    obs.robot_failure("nav_failed", "in a test")
    assert not (tmp_path / "spool").exists()


@pytest.mark.parametrize("role,env,expect", [
    ("robot", {"SENTRY_PROFILES_SAMPLE_RATE": "1.0"}, 0.0),                     # a copied .env can't turn it on
    ("robot", {"SENTRY_ROBOT_PROFILES_SAMPLE_RATE": "0.1"}, 0.1),               # only its own name can
    ("laptop", {"SENTRY_PROFILES_SAMPLE_RATE": "1.0"}, 1.0),
])
def test_the_robot_is_never_profiled_by_accident(tmp_path, role, env, expect):
    child = ("import os, sys; sys.path.insert(0, os.environ['ROOT']); import obs, sentry_sdk; "
             f"obs.init({role!r}); print(sentry_sdk.get_client().options['profiles_sample_rate'])")
    e = {"ROOT": str(ROOT), "PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "SENTRY_DSN": "http://k@127.0.0.1:9/1", **env}
    r = subprocess.run([sys.executable, "-c", child], env=e, capture_output=True, text=True, timeout=60)
    assert float(r.stdout.strip().splitlines()[-1]) == expect, r.stderr[-500:]
