#!/usr/bin/env python3
"""The Elastic artifact: ONE query -- BM25 + Jina dense + RRF + Jina rerank -- and the object
the vector leg found that BM25 could not.

    .venv/bin/python demo_hybrid.py mug          # print it (screenshot this)
    .venv/bin/python demo_hybrid.py mug --save   # also write artifacts/hybrid_mug.{json,txt}

The request printed is queries.hybrid_request(), byte for byte what search_objects() sends.
The two single-leg lookups after it are the evidence for "BM25 missed it": the same BM25 and
dense clauses, run alone (tests/test_query_shapes.py pins that they are the same clauses).
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

import setup_elastic as S
from queries import Queries

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from roomctl.repo import default_path  # noqa: E402  (the room's own repository)

NOISE_QUERY = "xylophone"  # nothing in the room: its best dense score is what "no match" looks like
SYNTHETIC = ("fake/", "scripts/", "tests/")  # vlm_model prefixes of generated (not model-written) text


def provenance_banner(hits: list[dict]) -> str:
    """Say which parts of this screen are real and which are generated -- never leave it implied."""
    models = sorted({h.get("vlm_model") or "(unknown)" for h in hits})
    synthetic = [m for m in models if m == "(unknown)" or m.startswith(SYNTHETIC)]
    if not synthetic:
        return (f"PROVENANCE: descriptions written by {', '.join(models)} from real camera captures; "
                f"the search below ran live.")
    return ("PROVENANCE: the descriptions below are SYNTHETIC -- scripted by "
            f"{', '.join(synthetic)} as a stand-in for the camera VLM (vlm_model says so on every document).\n"
            "REAL: the git commits in room.git, the capture -> per-camera views -> commit chain, and the search\n"
            "itself -- BM25, Jina embeddings, RRF and the Jina rerank all ran live on this cluster over that text.")


def run(q: Queries, text: str, size: int) -> dict:
    request = q.hybrid_request(text, size=size)
    hits = q.search_objects(text, size=size)
    bm25 = q.lexical_only(text)
    dense = q.semantic_only(text)
    floor = q.semantic_only(NOISE_QUERY, size=1)
    return {"text": text, "request": request, "hits": hits, "bm25": bm25, "dense": dense,
            "noise_floor": floor[0][1] if floor else None,
            "provenance": provenance(hits), "cameras": cameras(q, hits, bm25, dense)}


def provenance(hits: list[dict]) -> dict:
    """Every commit behind these results must exist in room.git -- no hand-made or stray docs."""
    repo = default_path()
    shas = sorted({t["commit_sha"] for h in hits for t in h["timeline"]} | {h["commit_sha"] for h in hits})
    missing = [s for s in shas if subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"{s}^{{commit}}"],
                                                 capture_output=True).returncode != 0]
    return {"repo": str(repo), "commits": len(shas), "not_in_repo": missing}


def cameras(q: Queries, hits: list[dict], bm25: list[str], dense: list) -> dict:
    """For the object BM25 missed: which cameras' raw views ARE the descriptions shown."""
    dense_ids = {oid for oid, _ in dense}
    hit = next((h for h in hits if h["object_id"] not in bm25 and h["object_id"] in dense_ids), None)
    if not hit:
        return {}
    obs = q.es.search(index=q.observations, size=20, _source=["camera", "raw_description"],
                      query={"bool": {"filter": [{"term": {"object_id": hit["object_id"]}},
                                                 {"term": {"capture_id": hit["capture_id"]}}]}})
    views = {o["_source"]["camera"]: o["_source"].get("raw_description") for o in obs["hits"]["hits"]}
    shown = set(hit["raw_description"] or [])
    return {"object_id": hit["object_id"], "capture_id": hit["capture_id"], "commit_sha": hit["commit_sha"],
            "by_camera": {cam: v for cam, v in sorted(views.items()) if v in shown},
            "all_traced": bool(shown) and shown <= set(views.values())}


def show(r: dict) -> None:
    text, dense_rank = r["text"], {oid: (i + 1, s) for i, (oid, s) in enumerate(r["dense"])}
    print(f'ONE QUERY  GET room-objects/_search   "{text}"\n')
    print(json.dumps(r["request"], indent=1))
    print(f'\nRESULT  (collapsed: one hit per object, its whole history inside)\n')
    print(provenance_banner(r["hits"]) + "\n")
    print(f"{'#':>2}  {'object':<18} {'class':<14} {'rerank':>7}   {'BM25':<5} {'dense':<21} {'text by':<16} descriptions")
    for i, h in enumerate(r["hits"], 1):
        bm = "hit" if h["object_id"] in r["bm25"] else "MISS"
        rank, score = dense_rank.get(h["object_id"], (None, None))
        dn = f"#{rank} {score:.3f}" if rank else "-"
        if score is not None and r["noise_floor"] is not None and score < r["noise_floor"]:
            dn += " (noise)"  # below the best score a query for something absent gets
        desc = h["raw_description"] if isinstance(h["raw_description"], list) else [h["raw_description"]]
        print(f"{i:>2}  {h['object_id']:<18} {h['class']:<14} {h['score']:>7.3f}   {bm:<5} {dn:<21} "
              f"{h.get('vlm_model') or '(unknown)':<16} "
              + " | ".join(d for d in desc if d))
    missed = [h for h in r["hits"] if h["object_id"] not in r["bm25"] and h["object_id"] in dense_rank]
    print(f'\nBM25 alone ("{text}" over class + every description) matches: {r["bm25"]}')
    if r["noise_floor"] is not None:
        print(f'Dense noise floor: best score for "{NOISE_QUERY}" (not in the room) = {r["noise_floor"]:.3f}')
    if missed:
        top = missed[0]
        rank, score = dense_rank[top["object_id"]]
        print(f"\n=> {top['object_id']} ({top['class']}): BM25 MISSED it -- no \"{text}\" in its class or any "
              f"description -- the Jina vector leg ranked it #{rank} ({score:.3f}), and after RRF + "
              f"rerank it is #{r['hits'].index(top) + 1}.")
    cam = r.get("cameras") or {}
    if cam.get("object_id") == (missed[0]["object_id"] if missed else None):
        print(f"   Its descriptions are its cameras' views at capture {cam['capture_id']} "
              f"(commit {cam['commit_sha'][:8]}; text by {top.get('vlm_model') or 'unknown'}): " + "; ".join(f"{c}: \"{v}\"" for c, v in cam["by_camera"].items())
              + ("" if cam["all_traced"] else "  [NOT all traced to a camera]"))
    p = r.get("provenance") or {}
    if p:
        print(f"\nProvenance: {p['commits']} commits behind these results; "
              + ("all exist in room.git." if not p["not_in_repo"] else f"NOT in room.git: {p['not_in_repo']}"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="?", default="mug")
    ap.add_argument("--size", type=int, default=5)
    ap.add_argument("--save", action="store_true", help="write artifacts/hybrid_<text>.{json,txt}")
    args = ap.parse_args()
    try:
        es = S.connect()
    except S.SetupError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    r = run(Queries(es), args.text, args.size)
    out = io.StringIO()
    with redirect_stdout(out):
        show(r)
    print(out.getvalue(), end="")
    if args.save:
        d = Path(__file__).resolve().parent / "artifacts"
        d.mkdir(exist_ok=True)
        stem = d / f"hybrid_{args.text.replace(' ', '_')}"
        version = es.info()["version"]
        meta = {"captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "cluster": f"{version.get('build_flavor')} {version['number']}"}
        stem.with_suffix(".json").write_text(json.dumps({**meta, **r}, indent=1, default=str))
        stem.with_suffix(".txt").write_text(out.getvalue())
        print(f"\nsaved {stem.with_suffix('.json').name}, {stem.with_suffix('.txt').name} in {d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
