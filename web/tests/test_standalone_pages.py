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
