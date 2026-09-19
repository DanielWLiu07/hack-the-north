#!/usr/bin/env python3
"""
story_demo.py — produce the Sentry + Elasticsearch story, end to end, for real.

Simulates one bad capture: the robot was mid-lean when the shutter fired, so
cam2's cloud is skewed, the diff comes out wrong, and a grasp slips.

Then proves the bidirectional link:
    Sentry trace  --capture_id-->  Elasticsearch documents
    Elastic doc   --trace_id--->   the Sentry waterfall

The Elasticsearch half is real: the documents are bulk-indexed (target index on the
bulk action line, never inside the document) and then read back by sentry_trace_id and
by capture_id with the same queries the agent uses. Exit 0 only if both lookups return
exactly what was written. story_docs.json keeps the bulk actions that were sent.

Run:  .venv/bin/python scripts/story_demo.py      (needs the elasticsearch client)
      then elastic/setup_elastic.py must have run once against the cluster
"""
import os, sys, time, json, random, datetime as dt, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

for line in (ROOT / ".env").read_text().splitlines():
    if line.strip() and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())

import obs

CAPTURE_ID = f"cap_{int(time.time())%100000:05d}"
COMMIT_SHA = "a3f9c1e"
now = lambda: dt.datetime.now(dt.timezone.utc).isoformat()

print(f"\n  capture_id = {CAPTURE_ID}   commit = {COMMIT_SHA}\n")
live = obs.init("laptop")
print(f"  sentry: {'connected' if live else 'NO DSN — running dry'}")

# 50 Hz telemetry for the two seconds before the shutter. The robot is
# recovering from a balance correction: the residual spikes at t-200ms.
telemetry = []
for i in range(100):
    t_rel = -2.0 + i * 0.02
    spike = 0.9 if -0.25 < t_rel < -0.15 else 0.0
    telemetry.append({
        "t_rel_s": round(t_rel, 3),
        "pitch": round(0.02 + spike * 0.11 + random.uniform(-.004, .004), 4),
        "tilt_rate": round(0.004 + spike * 0.26 + random.uniform(-.002, .002), 4),
        "odom_residual": round(0.004 + spike * 0.048, 4),
    })
tilt_rate_max = max(s["tilt_rate"] for s in telemetry)
skew_ms, coverage = 3.1, 0.71

es_docs = []   # (index, document) -- the index goes on the bulk action line, never in the doc

with obs.transaction("room.status", f"room status [{CAPTURE_ID}]"):
    with obs.capture_scope(CAPTURE_ID, COMMIT_SHA) as link:
        print(f"  trace : {link.get('sentry_trace_id') if live else '(none: Sentry not initialised)'}")

        with obs.span("robot.capture", "3x stereo grab/retrieve",
                      cameras=3, frames=4):
            time.sleep(0.08)
            ok = obs.capture_quality(skew_ms, tilt_rate_max, coverage)
            print(f"  gate  : skew={skew_ms}ms tilt_rate_max={tilt_rate_max:.3f} "
                  f"coverage={coverage} -> {'PASS' if ok else 'REJECT'}")

        with obs.span("perception.sgbm", "disparity x3"):      time.sleep(0.12)
        with obs.span("perception.segment", "SAM3 masks"):     time.sleep(0.09)

        # the three cameras disagree about the mug — THE messy-data artifact
        with obs.span("perception.merge", "cross-camera merge"):
            # raw_x/y/z per camera (the fake/README.md contract). x carries the 49 mm story.
            per_cam = [("cam0", (0.421, 0.312, 0.791), 0.93, "a blue ceramic mug"),
                       ("cam1", (0.418, 0.309, 0.788), 0.88, "cup with handle, chipped"),
                       ("cam2", (0.467, 0.318, 0.795), 0.41, "cylindrical container, dark")]
            spread_mm = (max(p[1][0] for p in per_cam) - min(p[1][0] for p in per_cam)) * 1000
            print(f"  merge : cam disagreement on mug_a1b2 = {spread_mm:.0f} mm "
                  f"(quantum is 10 mm)")
            for cam, (x, y, z), conf, desc in per_cam:
                es_docs.append(("room-observations", {
                    "@timestamp": now(), "capture_id": CAPTURE_ID, "object_id": "mug_a1b2",
                    "camera": cam, "raw_x": x, "raw_y": y, "raw_z": z,
                    "confidence": conf, "raw_description": desc,
                    "occluded": False, "vlm_model": "scripts/story_demo",  # synthetic, labelled
                    **obs.trace_fields()}))

        with obs.span("es.associate", "hybrid search re-identify"): time.sleep(0.05)
        with obs.span("git.commit", "write snapshot"):              time.sleep(0.03)

        es_docs.append(("room-clouds", {
            "@timestamp": now(), "capture_id": CAPTURE_ID, "commit_sha": COMMIT_SHA,
            "skew_ms": skew_ms, "tilt_rate_max": tilt_rate_max,
            "coverage_pct": coverage, "quality_ok": ok, **obs.trace_fields()}))

# the grasp that slipped, as a real Sentry issue with telemetry breadcrumbs
eid = obs.robot_failure(
    "grasp_slipped",
    "gripper closed to 2 mm, expected 78 mm — target pose derived from a skewed cloud",
    telemetry=telemetry, capture_id=CAPTURE_ID, object_id="mug_a1b2",
    commit_sha=COMMIT_SHA)
print(f"  issue : grasp_slipped -> {eid}")

obs.flush(15)

if not live:
    # Without a DSN the SDK still mints trace ids, but Sentry never hears of them: indexing
    # them would plant links that lead nowhere.
    for _, doc in es_docs:
        for k in ("sentry_trace_id", "sentry_span_id", "sentry_url"):
            doc.pop(k, None)
trace_id = es_docs[0][1].get("sentry_trace_id")
sent = {}
for index, _ in es_docs:
    sent[index] = sent.get(index, 0) + 1


def index_and_read_back() -> str | None:
    """Bulk-index es_docs, then prove both directions of the join from Elasticsearch itself.
    Returns None on success, else what failed."""
    sys.path.insert(0, str(ROOT / "elastic"))
    try:
        import ingest, setup_elastic
        from queries import Queries
    except ImportError as e:
        return f"{e.name} is not installed here -- run with .venv/bin/python"
    actions = [ingest.action(index, doc) for index, doc in es_docs]
    out = ROOT / "story_docs.json"
    out.write_text(json.dumps(actions, indent=2))
    print(f"\n  file  : {out.name} = the {len(actions)} bulk actions (_index on the action, not the doc)")
    try:
        es = setup_elastic.connect()
    except setup_elastic.SetupError as e:
        return str(e)
    tally = ingest.write(es, actions)  # refresh=wait_for: searchable when it returns
    errors = [f"{i}: {errs[0]}" for i, (_, _, errs) in tally.items() if errs]
    if errors:
        return "bulk rejected -- " + "; ".join(errors)
    print("  index : " + ", ".join(f"{i} {ok}" for i, (ok, _, _) in sorted(tally.items())))

    q = Queries(es)
    for label, found in ((f"capture_id {CAPTURE_ID}", q.capture_docs(CAPTURE_ID)),
                         (f"sentry_trace_id {trace_id}", q.trace_docs(trace_id) if trace_id else None)):
        if found is None:
            return "Sentry not initialised (SENTRY_DSN unset), so no trace to join -- trace -> Elastic unproven"
        got = {i: len(d) for i, d in found.items()}
        print(f"  read  : {label} -> " + ", ".join(f"{i} {n}" for i, n in sorted(got.items())))
        if got != sent:
            return f"read back {got} by {label}, but wrote {sent}"
    return None


failure = index_and_read_back()
if failure:
    print(f"\n  ELASTICSEARCH HALF NOT DONE: {failure}")
    print("  The Sentry half above is real; the join below is NOT proven until this passes.")
else:
    print(f"\n  joined: both lookups return exactly the {sum(sent.values())} documents written")
print("\n  THE STORY")
print("  ─────────")
print(f"  1. Sentry trace shows tilt_rate_max={tilt_rate_max:.3f} rad/s at capture")
print(f"     — the robot was mid-lean. The quality gate REJECTED it.")
print(f"  2. Elasticsearch shows the three cameras disagreeing by {spread_mm:.0f} mm")
print(f"     on one object, when our quantum is 10 mm.")
print(f"  3. The grasp then slipped, because its target came from that cloud.")
print(f"  4. Every ES doc carries sentry_trace_id; every span carries capture_id.")
print(f"     Either system leads to the other in one click.\n")
sys.exit(1 if failure else 0)
