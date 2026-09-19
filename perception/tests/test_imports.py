"""perception/ is imported two ways, often in ONE process: flat (`import voxelize`, with
perception/ on sys.path: pipeline, roomctl/publish._import, the tests) and as a package
(`from perception.voxelize import ...`: elastic/records.py, the commit hook's objects leg).

The regression (docs/10, 02:27): voxelize grew a flat `from es_sink import ...`, the package
style raised ModuleNotFoundError, tests/test_publish.py went 11/18 failing, and elastic/ papered
over it by appending perception/ to sys.path. These pin the source fix. Each case is a fresh
interpreter, because the bug lives in sys.modules."""
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def run(*parts: str) -> str:
    code = "\n".join(textwrap.dedent(p) for p in parts)
    r = subprocess.run([sys.executable, "-c", code], cwd="/", capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "PYTHONDONTWRITEBYTECODE": "1"})
    assert r.returncode == 0, r.stderr[-2000:]
    return r.stdout.strip()


ONE_SINK = """
    mods = {n: m for n, m in sys.modules.items() if n in ("es_sink", "perception.es_sink")}
    assert len(mods) == 2 and len({id(m) for m in mods.values()}) == 1, {n: id(m) for n, m in mods.items()}
    a, b = sys.modules["es_sink"], sys.modules["perception.es_sink"]
    try:
        raise a.Offline("x")
    except b.Offline:
        print("one es_sink")
"""


def test_package_import_needs_nothing_but_the_repo_root():
    """elastic/records.py's way, WITHOUT its old sys.path workaround."""
    out = run(f"""
        import sys
        sys.path.insert(0, {str(ROOT)!r})
        import perception.voxelize as v
        assert not any(p.rstrip('/').endswith('/perception') for p in sys.path), sys.path
        print(v.octree_key(0.1, 0.2, 0.3, (-4.0, -4.0, 0.0), 8.0), v.IndexResult.__module__)
    """)
    assert out.endswith("es_sink")


def test_flat_import_still_works():
    out = run(f"""
        import sys
        sys.path.insert(0, {str(ROOT / 'perception')!r})
        import voxelize
        print(voxelize.IndexResult.__name__)
    """)
    assert out == "IndexResult"


@pytest.mark.parametrize("first", ["flat", "package"])
def test_both_styles_in_one_process_share_one_es_sink(first):
    """The commit hook: roomctl/publish._import('perception', 'voxelize') (flat) and
    elastic/records.py (perception.voxelize) in one process, either order."""
    flat = "from roomctl import publish; publish._import('perception', 'voxelize')"
    package = "import perception.voxelize"
    steps = (flat, package) if first == "flat" else (package, flat)
    assert run(f"""
        import sys
        sys.path.insert(0, {str(ROOT)!r})
        {steps[0]}
        {steps[1]}
    """, ONE_SINK) == "one es_sink"


def test_pipeline_importing_es_sink_first_gets_the_same_module():
    """pipeline.py does `import es_sink` flat before it ever touches voxelize."""
    assert run(f"""
        import sys
        sys.path[:0] = [{str(ROOT / 'perception')!r}, {str(ROOT)!r}]
        import es_sink
        import perception.voxelize as v
        assert v.es_sink is es_sink
    """, ONE_SINK) == "one es_sink"


def test_elastic_records_imports_without_any_perception_path_hack():
    out = run(f"""
        import sys
        sys.path[:0] = [{str(ROOT / 'elastic')!r}, {str(ROOT)!r}]
        import records
        assert not any(p.rstrip('/').endswith('/perception') for p in sys.path), sys.path
        print(records.octree_key.__module__)
    """)
    assert out == "perception.voxelize"
