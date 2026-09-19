"""server.py's standalone pages: /robot is reserved for pages/robot.html (built by another session)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


def test_robot_is_reserved_and_honest_until_the_file_exists(monkeypatch, tmp_path):
    api = TestClient(server.app)
    (tmp_path / "pages").mkdir()
    monkeypatch.setattr(server, "HERE", tmp_path)
    for url in ("/robot", "/pages/robot.html"):
        r = api.get(url)
        assert r.status_code == 404 and r.json() == {"error": "not_found", "detail": "pages/robot.html has not been written yet", "retryable": False}
    (tmp_path / "pages" / "robot.html").write_text("<!doctype html><title>robot</title>")
    for url in ("/robot", "/pages/robot.html"):
        r = api.get(url)
        assert r.status_code == 200 and "<title>robot</title>" in r.text and r.headers["cache-control"] == "no-cache"
    assert api.get("/pages/capture.html").status_code == 404, "only the allow-listed page is served as .html"


def test_a_mistyped_url_gets_a_page_for_people_and_json_for_programs():
    api = TestClient(server.app)
    human = api.get("/no-such-page<script>", headers={"accept": "text/html,application/xhtml+xml"})
    assert human.status_code == 404 and human.headers["content-type"].startswith("text/html")
    assert "Nothing is kept at this address" in human.text and 'href="/robot"' in human.text and "<script>" not in human.text.split("<main")[1]
    program = api.get("/no-such-page")
    assert program.status_code == 404 and program.json() == {"error": "not_found", "detail": "Not Found", "retryable": False}
    under_api = api.get("/api/no-such-endpoint", headers={"accept": "text/html"})
    assert under_api.status_code == 404 and under_api.json()["error"] == "not_found", "/api/* is always the JSON shape"


def test_camera_frames_under_live_are_for_this_laptop_only(monkeypatch, tmp_path):
    """landing/live/ is what camera_ingest.py received: real frames of a real room. A tunnel's request also comes
    from 127.0.0.1, so local = a loopback peer AND no forwarding header (the rule robot_view_api.py uses)."""
    scope = lambda peer, headers=(): {"client": (peer, 5555), "headers": [(k.encode(), b"x") for k in headers]}   # noqa: E731
    local = server.LandingFiles.is_local
    assert local(scope("127.0.0.1")) and local(scope("::1"))
    assert not local(scope("203.0.113.9")), "another machine on the network"
    for via in ("x-forwarded-for", "cf-connecting-ip", "X-Forwarded-Proto", "forwarded", "x-vercel-forwarded-for", "tailscale-user-login"):
        assert not local(scope("127.0.0.1", [via])), f"a tunnel arrives from loopback too: {via}"

    live = tmp_path / "live" / "cap_0001"
    live.mkdir(parents=True)
    (tmp_path / "live" / "latest.json").write_text('{"capture_id": "cap_0001"}')
    (live / "cam1_color.jpg").write_bytes(b"\\xff\\xd8not really a photo")
    (tmp_path / "ok.json").write_text("{}")
    import asyncio
    files = server.LandingFiles(directory=tmp_path, html=True)
    get = lambda path, sc: asyncio.run(files.get_response(path, {**sc, "type": "http", "method": "GET", "path": "/" + path}))   # noqa: E731
    for path in ("live/latest.json", "live/cap_0001/cam1_color.jpg", "LIVE/latest.json"):
        with __import__("pytest").raises(server.StarletteHTTPException) as e:
            get(path, scope("127.0.0.1", ["x-forwarded-for"]))
        assert e.value.status_code == 403
    mine = get("live/latest.json", scope("127.0.0.1"))
    assert mine.status_code == 200 and mine.headers["cache-control"] == "no-store", "never kept by a shared cache"
    assert get("ok.json", scope("127.0.0.1", ["x-forwarded-for"])).status_code == 200, "everything else stays public"
