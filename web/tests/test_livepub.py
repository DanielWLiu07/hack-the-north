"""Publishing the live camera to a public copy: it is OFF, it is brief, and it never shows a stale frame.

These are the safety properties, not the feature. A camera in someone's room being served to strangers is
worth testing from the outside: that nothing is stored while it is off, that "off" DROPS the bytes rather
than hiding them, that a window cannot outlive its cap, and that a publisher which stalls stops being live
rather than leaving a frozen picture that reads as now.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import livepub  # noqa: E402
import server  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64        # enough to be a JPEG by its magic; the module never decodes one
TOKEN = "t" * 40


@pytest.fixture(autouse=True)
def _closed(monkeypatch):
    """Every test starts with the switch off and nothing held."""
    monkeypatch.setattr(livepub, "_until", 0.0)
    monkeypatch.setattr(livepub, "_frame", None)
    monkeypatch.setattr(livepub, "_frame_at", 0.0)
    monkeypatch.setattr(livepub, "_seen", 0)
    monkeypatch.setenv("GITIRL_CLOUD_TOKEN", "")
    yield


@pytest.fixture()
def op():
    return TestClient(server.app, client=("127.0.0.1", 50000))     # the operator: loopback


@pytest.fixture()
def visitor():
    return TestClient(server.app)                                   # not loopback: the public copy's reader


def test_off_is_the_default_and_says_what_it_does_have(visitor):
    s = visitor.get("/api/live/state").json()
    assert s["publishing"] is False and s["live"] is False and s["frame_age_s"] is None and s["seconds_left"] == 0
    assert "stays on the operator's machine" in s["detail"], "the page needs the sentence, not just a false"
    r = visitor.get("/api/live/frame.jpg")
    assert r.status_code == 404 and r.json()["error"] == "not_publishing"


def test_a_frame_offered_while_it_is_off_is_refused_and_not_stored(op, visitor):
    r = op.post("/api/live/frame", content=JPEG, headers={"content-type": "image/jpeg"})
    assert r.status_code == 409 and r.json()["error"] == "not_publishing"
    assert livepub._frame is None, "nothing may be held while the switch is off"          # noqa: SLF001
    assert visitor.get("/api/live/frame.jpg").status_code == 404


def test_publishing_then_stopping_DROPS_the_frame_rather_than_hiding_it(op, visitor):
    assert op.post("/api/live/publish", json={"seconds": 60}).json()["publishing"] is True
    assert op.post("/api/live/frame", content=JPEG, headers={"content-type": "image/jpeg"}).status_code == 200
    got = visitor.get("/api/live/frame.jpg")
    assert got.status_code == 200 and got.content == JPEG and got.headers["cache-control"] == "no-store"
    assert visitor.get("/api/live/state").json()["live"] is True

    op.post("/api/live/publish/stop")
    assert livepub._frame is None, "the bytes must GO on stop: a hidden last frame is the whole risk"  # noqa: SLF001
    assert visitor.get("/api/live/frame.jpg").status_code == 404
    s = visitor.get("/api/live/state").json()
    assert s["publishing"] is False and s["live"] is False and s["frames"] == 0


def test_the_window_expires_by_itself_and_takes_the_frame_with_it(op, visitor, monkeypatch):
    op.post("/api/live/publish", json={"seconds": 60})
    op.post("/api/live/frame", content=JPEG, headers={"content-type": "image/jpeg"})
    assert visitor.get("/api/live/frame.jpg").status_code == 200

    now = livepub.time.monotonic()
    monkeypatch.setattr(livepub.time, "monotonic", lambda: now + 61)    # the window ends; nobody pressed anything
    assert visitor.get("/api/live/frame.jpg").status_code == 404
    assert livepub._frame is None, "expiry drops the bytes, it does not merely stop serving them"      # noqa: SLF001
    assert visitor.get("/api/live/state").json()["publishing"] is False


def test_a_stalled_publisher_stops_being_live_instead_of_freezing(op, visitor, monkeypatch):
    """The window is still open, but nothing has arrived. A frozen picture must not read as now."""
    op.post("/api/live/publish", json={"seconds": 600})
    op.post("/api/live/frame", content=JPEG, headers={"content-type": "image/jpeg"})
    now = livepub.time.monotonic()
    monkeypatch.setattr(livepub.time, "monotonic", lambda: now + livepub.STALE_S + 1)
    r = visitor.get("/api/live/frame.jpg")
    assert r.status_code == 404 and r.json()["error"] == "stale"
    s = visitor.get("/api/live/state").json()
    assert s["publishing"] is True and s["live"] is False, "still open, but not live — both facts are true"


def test_a_window_cannot_outlive_the_cap_however_long_it_asks_for(op):
    s = op.post("/api/live/publish", json={"seconds": 24 * 3600}).json()
    assert s["seconds_left"] <= livepub.MAX_S and s["capped"] is True
    assert op.post("/api/live/publish", json={"seconds": "all night"}).status_code == 422
    # extending replaces the deadline rather than adding to it
    again = op.post("/api/live/publish", json={"seconds": 30}).json()
    assert again["seconds_left"] <= 30


def test_only_the_operator_may_publish_and_a_visitor_may_only_look(visitor, op, monkeypatch):
    for path, body in (("/api/live/publish", {"seconds": 60}), ("/api/live/publish/stop", {})):
        assert visitor.post(path, json=body).status_code == 403, "no token configured: local only"
    assert visitor.post("/api/live/frame", content=JPEG, headers={"content-type": "image/jpeg"}).status_code == 403
    monkeypatch.setenv("GITIRL_CLOUD_TOKEN", TOKEN)
    assert visitor.post("/api/live/publish", json={"seconds": 60}).status_code == 401
    assert visitor.post("/api/live/publish", json={"seconds": 60},
                        headers={"authorization": f"Bearer {TOKEN}"}).status_code == 200
    # a tunnel arrives from 127.0.0.1 too: its forwarding header gives it away
    assert op.post("/api/live/publish/stop", headers={"x-forwarded-for": "203.0.113.9"}).status_code == 401
    # the state is readable by anyone — a person in the room is entitled to know their camera is being published
    assert visitor.get("/api/live/state").status_code == 200


def test_the_body_must_be_a_jpeg_and_a_small_one(op):
    op.post("/api/live/publish", json={"seconds": 60})
    assert op.post("/api/live/frame", content=b"<html>not a frame</html>").status_code == 415
    assert op.post("/api/live/frame", content=b"").status_code == 422
    assert op.post("/api/live/frame", content=b"\xff\xd8" + b"0" * livepub.MAX_BYTES).status_code == 413
    assert livepub._frame is None, "nothing that failed validation is ever held"                       # noqa: SLF001
