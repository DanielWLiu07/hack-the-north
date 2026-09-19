#!/usr/bin/env python3
"""
sentry_watch.py — watch Sentry and capture EVIDENCE when something interesting happens.

Sentry judges on "the debugging story, the 4am one. That's the submission." You cannot
reconstruct those on Sunday morning, and nobody remembers to write them down at 4am.
So this watches for them and writes them down itself.

On each new/changed issue it captures, into SENTRY_STORY.md and evidence/:
  - the issue, its tags, and its permalink
  - the breadcrumbs (for a robot failure, that is the telemetry before the fall)
  - any ATTACHMENTS downloaded to evidence/  ← the camera frame at the moment of failure
  - the linked capture_id and sentry_trace_id, so it joins to Elasticsearch

Run:  python3 scripts/sentry_watch.py            # one pass
      python3 scripts/sentry_watch.py --follow   # keep watching
"""
import os, sys, json, time, pathlib, urllib.request, urllib.parse, argparse

ROOT = pathlib.Path(__file__).resolve().parent.parent
for line in (ROOT/".env").read_text().splitlines():
    if line.strip() and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())

TOK  = os.environ["SENTRY_AUTH_TOKEN"]
ORG  = os.environ["SENTRY_ORG_SLUG"]
PROJ = "gitspace"
EV   = ROOT/"evidence"; EV.mkdir(exist_ok=True)
SEEN = ROOT/".sentry_watch_seen.json"
STORY= ROOT/"SENTRY_STORY.md"

def api(path, q=None, raw=False):
    u = f"https://sentry.io/api/0{path}" + (("?"+urllib.parse.urlencode(q, doseq=True)) if q else "")
    r = urllib.request.Request(u, headers={"Authorization": f"Bearer {TOK}"})
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return x.read() if raw else json.loads(x.read() or b"null")
    except Exception as e:
        return None if raw else {"_err": str(e)[:90]}

# Issues from the fake robot / mock arm (verification runs, the heal demo) are evidence that the
# wiring works, not debugging stories — writing them into SENTRY_STORY.md would pass a test off
# as a finding.
SYNTHETIC_FW = {"fake-robot", "mock-arm"}

def interesting(issue):
    """Not every error is a story. These are."""
    t = (issue.get("title") or "").lower()
    if issue.get("count", 0) and int(issue["count"]) >= 5:      return "recurring"
    for k in ("robot:", "grasp", "fell", "unreachable", "camera", "capture",
              "phantom", "drift", "occlu", "timeout", "cancel"):
        if k in t: return k.strip(":")
    return None

def capture(issue):
    gid = issue["id"]
    ev  = api(f"/issues/{gid}/events/latest/")
    tags = {t["key"]: t["value"] for t in (ev.get("tags") or [])} if isinstance(ev, dict) else {}
    crumbs = []
    ctx = {}
    if isinstance(ev, dict):
        for entry in ev.get("entries", []):
            if entry.get("type") == "breadcrumbs":
                crumbs = entry["data"].get("values", [])[-12:]
        ctx = ev.get("contexts", {}) or {}

    # attachments — the camera frame at the moment of failure
    files = []
    atts = api(f"/projects/{ORG}/{PROJ}/events/{ev.get('id')}/attachments/") if isinstance(ev, dict) else None
    for a in (atts or []):
        if not isinstance(a, dict): continue
        blob = api(f"/projects/{ORG}/{PROJ}/events/{ev['id']}/attachments/{a['id']}/?download=1", raw=True)
        if blob:
            p = EV/f"{gid}_{a['name']}"
            p.write_bytes(blob); files.append(p.name)
    return tags, crumbs, ctx, files

def write_entry(issue, kind, tags, crumbs, ctx, files):
    ts = time.strftime("%H:%M")
    lines = [f"\n## {ts} · AUTO-CAPTURED · {kind} · {issue['title'][:80]}",
             f"Seen: {issue.get('count','?')}× · first {issue.get('firstSeen','?')[:19]} · {issue['permalink']}"]
    join = {k: tags[k] for k in ("capture_id","commit_sha","camera","role","branch") if k in tags}
    if join: lines.append(f"Joins to: " + " · ".join(f"`{k}={v}`" for k,v in join.items()))
    q = ctx.get("capture_quality")
    if q: lines.append(f"Capture quality: {json.dumps({k:v for k,v in q.items() if k!='type'})}")
    if files: lines.append(f"**Evidence captured:** " + ", ".join(f"`evidence/{f}`" for f in files))
    if crumbs:
        lines.append("Telemetry before the failure (last few breadcrumbs):")
        lines.append("```")
        for c in crumbs[-6:]:
            lines.append(f"  {c.get('message','')[:40]:40s} {json.dumps(c.get('data') or {})[:90]}")
        lines.append("```")
    lines.append("_What we changed:_ TODO — fill this in, it is the part they score._")
    STORY.write_text(STORY.read_text() + "\n".join(lines) + "\n")

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--follow", action="store_true")
    ap.add_argument("--interval", type=int, default=120)
    a = ap.parse_args()
    seen = json.loads(SEEN.read_text()) if SEEN.exists() else {}
    while True:
        issues = api(f"/projects/{ORG}/{PROJ}/issues/", {"statsPeriod": "24h"})
        new = 0
        for i in (issues or []):
            if not isinstance(i, dict): continue
            kind = interesting(i)
            key = i["id"]
            if not kind: continue
            if seen.get(key) == i.get("count"):  continue     # unchanged
            tags, crumbs, ctx, files = capture(i)
            if tags.get("synthetic") == "true" or tags.get("fw") in SYNTHETIC_FW:
                seen[key] = i.get("count")          # a test or demo, not a story: never written up
                continue
            write_entry(i, kind, tags, crumbs, ctx, files)
            seen[key] = i.get("count"); new += 1
            print(f"  captured [{kind}] {i['title'][:56]}" + (f"  +{len(files)} file(s)" if files else ""))
        SEEN.write_text(json.dumps(seen))
        print(f"  {time.strftime('%H:%M:%S')} — {new} new/changed captured, {len(seen)} tracked")
        if not a.follow: return
        time.sleep(a.interval)

if __name__ == "__main__":
    main()
