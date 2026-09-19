"""server.py's optional routers: a missing one is skipped, a BROKEN one is skipped loudly — the site stays up."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402


def test_the_real_routers_loaded_and_voxel_api_is_reserved():
    j = TestClient(server.app).get("/api/routers").json()
    assert j["hot_reload"] is False and j["started"].endswith("Z")
    for name in ("capture_api", "dash_api", "graph_api", "object_api", "replay_api", "telemetry_api"):
        assert j["routers"][name].startswith("loaded ("), (name, j["routers"][name])
    assert "voxel_api" in j["routers"], "reserved: it mounts the first restart after web/voxel_api.py exists"


def test_a_missing_or_broken_router_never_takes_the_site_down(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(tmp_path))
    (tmp_path / "zz_syntax.py").write_text("def broken(:\n")
    (tmp_path / "zz_needs.py").write_text("import a_package_nobody_installed\n")
    (tmp_path / "zz_badinit.py").write_text("from fastapi import APIRouter\nrouter = APIRouter()\ndef init(es):\n    raise RuntimeError('no cluster for you')\n")
    (tmp_path / "zz_good.py").write_text("from fastapi import APIRouter\nrouter = APIRouter()\n@router.get('/api/zz')\ndef zz():\n    return {'ok': True}\ndef init(es):\n    pass\n")
    assert server.mount_router("zz_not_written_yet") == "not present"
    assert server.mount_router("zz_pkg.not_written") == "not present", "a missing PACKAGE is also just not there"
    assert server.mount_router("zz_syntax").startswith("FAILED: SyntaxError")
    assert server.mount_router("zz_needs").startswith("FAILED: ModuleNotFoundError") and "a_package_nobody_installed" in server.ROUTERS["zz_needs"]
    assert server.mount_router("zz_badinit") == "FAILED: RuntimeError: no cluster for you"
    assert server.mount_router("zz_good") == "loaded (1 routes)"
    api = TestClient(server.app)
    assert api.get("/api/health").status_code in (200, 503) and api.get("/api/routers").json()["routers"]["zz_syntax"].startswith("FAILED")


def test_health_goes_red_when_this_process_cannot_run_the_search(monkeypatch):
    """07:13Z on demo night: started with the system Python, no `elasticsearch` package, /api/search 503 —
    and /api/health said ok the whole time, because it only asked whether the cluster was up."""
    import es_shared

    async def info():
        return {"version": {"number": "9.6.0", "build_flavor": "serverless"}}
    monkeypatch.setattr(server.es, "info", info)
    api = TestClient(server.app)
    good = api.get("/api/health").json()
    assert good["ok"] is True and good["search"]["ok"] is True and good["search"]["python"]
    monkeypatch.setattr(es_shared, "IMPORT_ERROR", "ModuleNotFoundError: No module named 'elasticsearch'")
    bad = api.get("/api/health").json()
    assert bad["ok"] is False and bad["elastic"]["reachable"] is True, "the cluster is fine; THIS process is not"
    assert "../.venv/bin/python server.py" in bad["search"]["detail"] and "elasticsearch" in bad["search"]["detail"]
