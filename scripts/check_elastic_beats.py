#!/usr/bin/env python3
"""Every Elastic demo beat, against the live cluster, read-only. PASS/FAIL with the number that
decided it — so a beat that still "works" on a 0.04 margin is not called green.

    .venv/bin/python scripts/check_elastic_beats.py          # exit 0 only if every beat passes
    .venv/bin/python scripts/check_elastic_beats.py --branch main

Writes nothing, anywhere. Beats: find (hybrid search) · resolve (vague description) · the
showpiece (keywords miss the cup, the vector finds it) · blame · time travel · why (telemetry
behind the capture gate) · hidden-vs-gone.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "elastic"))

import setup_elastic as S  # noqa: E402
from queries import Queries  # noqa: E402

KEYS_PHRASINGS = ("where are my keys", "where did I leave my keys", "have you seen my keys")
MARGIN_MIN = 0.10   # a top-1 that wins by less is one new description away from flipping
WINDOW = "7d"       # observation history to look back over


def in_git(sha: str) -> bool:
    repo = ROOT / "room.git"
    return subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{sha}^{{commit}}"],
                          capture_output=True).returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--branch", default="main", help="the branch the demo runs on (default: main)")
    ap.add_argument("--object", default="mug_a1b2", help="object for the blame beat")
    a = ap.parse_args()
    try:
        es = S.connect()
    except S.SetupError as e:
        print(f"FAIL  cluster: {e}")
        return 2
    q, results = Queries(es), []

    def beat(name: str, fn) -> None:
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001 -- a broken beat is a result, not a crash
            ok, detail = False, f"{type(e).__name__}: {str(e)[:150]}"
        results.append(ok)
        print(f"  {'PASS' if ok else 'FAIL'}  {name:<22} {detail}")

    def find():
        worst, lines = None, []
        for text in KEYS_PHRASINGS:
            hits = q.search_objects(text, size=2, branch=a.branch)
            top = hits[0]["object_id"] if hits else None
            margin = hits[0]["score"] - hits[1]["score"] if len(hits) > 1 else 99.0
            lines.append(f"{text!r} -> {top} (+{margin:.3f})")
            if top != "keys_7c2e" or margin < MARGIN_MIN:
                worst = lines[-1]
        return worst is None, (f"3/3 top-1 keys_7c2e, margins "
                               f"{', '.join(l.split('(+')[1][:-1] for l in lines)}" if worst is None
                               else f"{worst}  (need keys_7c2e and margin >= {MARGIN_MIN})")

    def resolve():
        r = q.resolve_object("the thing I cut paper with", k=3, branch=a.branch)
        top = r["matches"][0] if r["matches"] else {}
        return top.get("object_id") == "scissors_9f3a", \
            f"{top.get('object_id')} in {top.get('zone')} (margin {r['margin']:.3f})" if r["margin"] else str(top)

    def showpiece():
        hits = [h["object_id"] for h in q.search_objects("mug", size=3, branch=a.branch)]
        bm25 = q.lexical_only("mug", branch=a.branch)
        dense = dict(q.semantic_only("mug", branch=a.branch))
        ok = "cup_7e21" in hits and "cup_7e21" not in bm25 and "cup_7e21" in dense
        return ok, (f"cup_7e21 #{hits.index('cup_7e21') + 1} of {len(hits)}, BM25 misses it, "
                    f"vector {dense.get('cup_7e21', 0):.3f}" if ok else f"hybrid {hits}, BM25 {bm25}")

    def blame():
        b = q.moved_at(a.object, branch=a.branch)
        if not b:
            return False, f"no snapshots for {a.object} on {a.branch}"
        m, views = b["moved_in"], b["frame"]["views"] if b["frame"] else []
        ok = bool(m["sha"]) and in_git(m["sha"])
        return ok, (f"{a.object} moved in {m['sha'][:8]} ({m['capture_id']}), "
                    f"{(b['from'] or {}).get('zone')} -> {b['to']['zone']}, {len(views)} camera views"
                    + ("" if ok else "  [sha not in room.git]"))

    def time_travel():
        now = datetime.now(timezone.utc)
        head = q.commit_at(now, branch=a.branch)
        if not head:
            return False, f"no commit events on {a.branch}"
        before = q.commit_at(datetime.fromisoformat(head["@timestamp"].replace("Z", "+00:00")),
                             branch=a.branch, strictly_before=True)
        ok = in_git(head["commit_sha"]) and (before is None or in_git(before["commit_sha"]))
        return ok, (f"now -> {head['commit_sha'][:8]} {head['message'][:28]!r}; strictly before it -> "
                    f"{before['commit_sha'][:8] if before else 'none'}")

    def why():
        cap = q.es.search(index=q.clouds, size=1, sort=[{"@timestamp": "desc"}],
                          _source=["capture_id", "@timestamp"])["hits"]["hits"]
        if not cap:
            return False, "no captures"
        c = cap[0]["_source"]
        sig = q.telemetry_window(c["@timestamp"], seconds=2.0)
        if sig:
            peak = max((s["peak"] for s in sig.values()), default=0)
            return True, f"{c['capture_id']}: {len(sig)} signals, peak |value| {peak:.3f}"
        newest = q._esql("FROM robot-telemetry | STATS to_at = MAX(@timestamp) | LIMIT 1")
        return False, (f"{c['capture_id']} ({c['@timestamp']}): no telemetry in its 2 s window; "
                       f"newest sample anywhere {newest[0]['to_at'] if newest else 'none'}")

    def hidden_or_gone():
        h = q.observation_history(a.object, window=WINDOW)
        cams = h["cameras"]
        ok = bool(cams)
        return ok, (f"{a.object}: {h['captures']} captures in {WINDOW}, "
                    + ", ".join(f"{c} {v['sightings']}x ({v['occluded']} occluded)" for c, v in cams.items())
                    if ok else f"{a.object}: no observations in {WINDOW}")

    print(f"Elastic demo beats · branch {a.branch} · {datetime.now(timezone.utc):%Y-%m-%d %H:%M}Z")
    for name, fn in (("find (keys)", find), ("resolve (vague)", resolve), ("showpiece (mug/cup)", showpiece),
                     ("blame (moved_at)", blame), ("time travel", time_travel), ("why (telemetry)", why),
                     ("hidden or gone", hidden_or_gone)):
        beat(name, fn)
    print(f"\n{sum(results)}/{len(results)} beats pass")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
