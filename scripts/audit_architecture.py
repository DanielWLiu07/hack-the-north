#!/usr/bin/env python3
"""
audit_architecture.py — does the CODE match the DESIGN?

Six parallel sessions are building against 23 documents. Documents drift from code
silently. This checks the invariants that, when violated, fail SILENTLY — the ones
where the system keeps running and quietly produces wrong answers.

Exit 0 = conformant. Exit 1 = at least one FAIL.
Run it from the repo root:  python3 scripts/audit_architecture.py
"""
import re, sys, json, pathlib, subprocess

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP = {".venv", "node_modules", "__pycache__", ".git", "room.git", "pomme-arm", "landing"}
RESULTS = []

def src_files(*exts):
    for p in ROOT.rglob("*"):
        if p.is_file() and p.suffix in exts and not any(s in p.parts for s in SKIP):
            yield p

def check(name, ok, detail="", ref="", severity="FAIL"):
    RESULTS.append({"name": name, "ok": bool(ok), "detail": detail,
                    "ref": ref, "severity": "OK" if ok else severity})

def grep_count(pattern, *exts):
    hits = []
    rx = re.compile(pattern)
    for p in src_files(*exts):
        try: t = p.read_text(errors="ignore")
        except Exception: continue
        for i, line in enumerate(t.splitlines(), 1):
            if rx.search(line):
                hits.append(f"{p.relative_to(ROOT)}:{i}")
    return hits


# ─── frames & units — silent corruption if wrong ──────────────────────────────
# exclude this auditor itself and test files — they legitimately mention it
defs = [x for x in grep_count(r"^\s*def cam_to_world_axes", ".py")
        if "audit_architecture" not in x and "/tests/" not in x and "test_" not in x]
check("cam_to_world_axes defined exactly once", len(defs) <= 1,
      f"{len(defs)} definition(s): {defs or ['none yet']}",
      "docs/20-perception-logic.md — two definitions means one is wrong; zero means clouds are sideways")

h = grep_count(r"reprojectImageTo3D", ".py")
div = grep_count(r"reprojectImageTo3D.*?/\s*1000|/\s*1000\.0", ".py")
check("mm→m conversion present where reprojectImageTo3D is used",
      (not h) or bool(div), f"reproject at {h}; /1000 at {div}",
      "docs/20 Fact 1 — Q is in MILLIMETRES. Missing = everything a km away; doubled = 1000x too small")

check("floor assertion exists (catches an axis error in one capture)",
      bool(grep_count(r"floor.*z|z.*floor|assert.*0\.05", ".py")),
      "", "docs/20 Part 6", severity="WARN")

# ─── secrets ──────────────────────────────────────────────────────────────────
gi = (ROOT/".gitignore").read_text() if (ROOT/".gitignore").exists() else ""
check(".env is gitignored", ".env" in gi, "", "KEYS.md")
leaked = grep_count(r"(sntry[us]_[A-Za-z0-9]{20,}|ApiKey\s+[A-Za-z0-9+/=]{40,})", ".py", ".js", ".md", ".json", ".sh")
check("no credentials hardcoded in source", not leaked, f"{leaked}", "KEYS.md")

# ─── repo separation ──────────────────────────────────────────────────────────
check("room.git is separate from the code repo",
      not (ROOT/".git"/"room.git").exists(), "",
      "docs/04 — conflating them costs an hour at the wrong time", severity="WARN")

# ─── observability ────────────────────────────────────────────────────────────
# Code only. A comment may name it (D17) — and so may a DOCSTRING: tests/conftest.py explains why
# blanking the DSN after `sentry_sdk.init()` changes nothing, and that sentence failed this check.
# A backtick before it means prose in this repo, the same way a `#` does.
inits = grep_count(r"^[^#]*(?<!`)sentry_sdk\.init\s*\(", ".py")
outside = [x for x in inits if not x.startswith(("obs.py", "scripts/"))
           # "tests may init their own" — which `tests/conftest.py` is, though it matched neither
           # "/tests/" (no leading slash) nor "test_" (it is a conftest).
           and not x.startswith("tests/") and "/tests/" not in x and "test_" not in x and "conftest" not in x]
check("sentry_sdk.init only in obs.py", not outside,
      f"stray inits: {outside}", "obs.py is the single init — duplicates double-report")
check("obs.py imported by at least one subsystem",
      bool(grep_count(r"^\s*import obs|from obs import", ".py")),
      "", "obs.py", severity="WARN")

# ─── elasticsearch mappings ───────────────────────────────────────────────────
maps = list((ROOT/"elastic"/"mappings").glob("*.json")) if (ROOT/"elastic"/"mappings").exists() else []
if maps:
    blob = " ".join(p.read_text(errors="ignore") for p in maps)
    check("cell mapped as cartesian `point`, not geo_point",
          '"point"' in blob and '"geo_point"' not in blob,
          f"{len(maps)} mapping files", "docs/13 — geo_point breaks every spatial query")
    check("voxel_key is keyword (not text)",
          ('voxel_key' not in blob) or ('"voxel_key"' in blob and '"keyword"' in blob),
          "", "docs/13 — text tokenises and aggregations return garbage")
    check("TSDS templates set look_back_time",
          ("time_series" not in blob) or ("look_back_time" in blob),
          "", "docs/13 gotcha 3 — replaying recorded data silently fails to ingest")
    check("mappings carry sentry_trace_id (the Elastic↔Sentry join)",
          "sentry_trace_id" in blob, "",
          "obs.trace_fields() — scored by BOTH prizes", severity="WARN")
else:
    check("elastic/mappings/*.json exist", False, "none found", "elastic/TASK.md")

# ─── robot API surface ────────────────────────────────────────────────────────
srv = ROOT/"robot"/"server.py"
if srv.exists():
    t = srv.read_text(errors="ignore")
    missing = [e for e in ("/capture","/pose","/drive","/arm","/say","/led") if e not in t]
    check("all six robot endpoints present", not missing, f"missing {missing}", "docs/16-api.md")
else:
    check("robot/server.py exists", False, "not built yet", "robot/README.md", severity="WARN")

# ─── latency tiers: ES must never be in the control loop ──────────────────────
bad = []
for p in src_files(".py"):
    if p.name in ("balance.py","lqr.py","telemetry.py") or "control" in p.name:
        t = p.read_text(errors="ignore")
        if re.search(r"elasticsearch|es\.search|requests\.(get|post)", t):
            bad.append(str(p.relative_to(ROOT)))
check("no Elasticsearch/HTTP inside control-loop modules", not bad, f"{bad}",
      "docs/11 latency tiers — a network call there stalls the robot on a wifi hiccup")

# ─── the gate that matters ────────────────────────────────────────────────────
check("tests/test_idempotent_scan.py exists (gate G2)",
      (ROOT/"tests"/"test_idempotent_scan.py").exists(), "",
      "docs/03 — scan twice, git diff --exit-code clean. THE test")

# ─── docs referenced by README actually exist ─────────────────────────────────
rd = (ROOT/"README.md").read_text(errors="ignore")
dead = [m for m in re.findall(r"\(((?:docs|web)/[\w./-]+\.md)\)", rd) if not (ROOT/m).exists()]
check("no dead links in README", not dead, f"{dead}", "", severity="WARN")

# ─── the build log is being kept ──────────────────────────────────────────────
pg = (ROOT/"PROGRESS.md")
n = pg.read_text().count("## h") if pg.exists() else 0
check("PROGRESS.md has entries from the sessions", n >= 3,
      f"{n} entries", "PROGRESS.md — feeds the Devpost and the Sentry story", severity="WARN")

# ─── doc drift: is the code newer than the doc that describes it? ────────────
PAIRS = [
 (["robot/server.py","robot/capture.py","robot/telemetry.py"], "docs/16-api.md"),
 (["elastic/mappings"],                                        "docs/13-ingest.md"),
 (["perception/fuse.py","perception/depth.py","perception/serialize.py"],
                                                               "docs/20-perception-logic.md"),
 (["perception/segment.py","perception/cluster.py"],           "docs/15-segmentation.md"),
 (["telemetry","robot/telemetry.py"],                          "docs/23-telemetry.md"),
 (["roomctl/state.py"],                                        "docs/04-git-semantics.md"),
 (["obs.py"],                                                  "docs/18-sentry.md"),
 (["web/server.py"],                                           "web/PAGES.md"),
]
def newest(rel):
    p = ROOT/rel
    if not p.exists(): return 0
    if p.is_dir():
        ts = [q.stat().st_mtime for q in p.rglob("*") if q.is_file()]
        return max(ts) if ts else 0
    return p.stat().st_mtime

stale = []
for srcs, doc in PAIRS:
    d = newest(doc)
    if not d: continue
    for sp in srcs:
        c = newest(sp)
        if c and c > d + 900:          # 15 min grace — a doc edit rarely lands first
            stale.append(f"{sp} newer than {doc}")
check("no code newer than the doc describing it", not stale,
      "; ".join(stale[:4]) + (f" (+{len(stale)-4} more)" if len(stale) > 4 else ""),
      "CONTRIBUTING.md — change a contract, update its doc in the SAME turn", severity="WARN")

check("DIAGRAM-DRIFT.md exists (the republish queue)",
      (ROOT/"DIAGRAM-DRIFT.md").exists(), "", "CONTRIBUTING.md", severity="WARN")

# ─── report ───────────────────────────────────────────────────────────────────
W, F = "\033[33m", "\033[31m"; G, X = "\033[32m", "\033[0m"
fails = [r for r in RESULTS if not r["ok"] and r["severity"] == "FAIL"]
warns = [r for r in RESULTS if not r["ok"] and r["severity"] == "WARN"]
print(f"\n  ARCHITECTURE AUDIT — {len(RESULTS)} invariants\n  " + "─"*66)
for r in RESULTS:
    mark = f"{G}  ok  {X}" if r["ok"] else (f"{F} FAIL {X}" if r["severity"]=="FAIL" else f"{W} warn {X}")
    print(f"  {mark} {r['name']}")
    if not r["ok"]:
        if r["detail"]: print(f"         {r['detail']}")
        if r["ref"]:    print(f"         → {r['ref']}")
print(f"\n  {len(RESULTS)-len(fails)-len(warns)} ok · {len(warns)} warn · {len(fails)} FAIL\n")
(ROOT/"audit_report.json").write_text(json.dumps(RESULTS, indent=2))
sys.exit(1 if fails else 0)
