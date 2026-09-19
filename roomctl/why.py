"""`room why <commit>`: was this commit's picture of the room trustworthy? (plan/roommate/03 §12)

The cross-index join, the Elastic and Sentry showpiece in one: a commit (room-events) points at
the capture that made it (room-clouds: the gate's skew and tilt verdict), which points at the
robot's telemetry in the seconds before the shutter (robot-telemetry, ES|QL), and every one of
those documents carries the Sentry trace id of the same moment.

    commit a3f9c1 — capture cap_0012, quality gate PASSED (skew 3.0 ms < 25, tilt 0.007 rad/s < 0.05)
      the robot was steady: peak tilt_rate 0.009 rad/s, pitch within 0.02 rad in the 2 s before
      trace 234f3164… (Sentry)

The thresholds are the robot's own (robot/config.py), so the explanation can never disagree
with the rule that accepted or rejected the capture.
"""
from __future__ import annotations

from roomctl.repo import GitError, Repo

try:
    from robot.config import MAX_SKEW_MS, MAX_TILT_RATE
except Exception:  # pragma: no cover — the robot package is optional on a laptop checkout
    MAX_SKEW_MS, MAX_TILT_RATE = 25.0, 0.05


def _one(es, index: str, query: dict, sort: str = "@timestamp") -> dict | None:
    r = es.search(index=index, size=1, query=query, sort=[{sort: "desc"}])
    hits = r["hits"]["hits"]
    return hits[0]["_source"] if hits else None


def explain(repo: Repo, ref: str, es, seconds: float = 2.0) -> dict:
    """Everything the indices know about the capture behind commit `ref`, and a verdict."""
    sha = repo.git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}", check=False).stdout.strip()
    if not sha:
        raise GitError(f"no commit {ref!r}")
    from roomctl.publish import _import
    Queries = _import("elastic", "queries").Queries   # elastic/queries.py
    q = Queries(es)
    event = _one(es, q.events, {"bool": {"filter": [{"term": {"commit_sha": sha}}, {"term": {"event_type": "commit"}}]}})
    capture_id = (event or {}).get("capture_id")
    cloud = _one(es, q.clouds, {"term": {"capture_id": capture_id}}) if capture_id else None
    at = (cloud or {}).get("@timestamp") or (event or {}).get("@timestamp")
    window = q.telemetry_window(at, seconds) if at else {}
    trace = next((d.get("sentry_trace_id") for d in (cloud, event) if d and d.get("sentry_trace_id")), None)
    url = next((d.get("sentry_url") for d in (cloud, event) if d and d.get("sentry_url")), None)

    findings: list[str] = []
    gate = None
    if cloud:
        skew, tilt = cloud.get("skew_ms"), cloud.get("tilt_rate_max")
        passed = bool(cloud.get("quality_ok"))
        gate = {"passed": passed, "skew_ms": skew, "tilt_rate_max": tilt, "max_skew_ms": MAX_SKEW_MS,
                "max_tilt_rate": MAX_TILT_RATE, "coverage_pct": cloud.get("coverage_pct")}
        if skew is not None and skew >= MAX_SKEW_MS:
            findings.append(f"the cameras were {skew:.1f} ms apart (the gate allows < {MAX_SKEW_MS:g} ms): "
                            f"the views are not one instant")
        if tilt is None:
            findings.append("the robot's tilt was not measured at the shutter: the gate could not vouch for it")
        elif tilt >= MAX_TILT_RATE:
            findings.append(f"the robot was leaning while it looked (tilt rate {tilt:.3f} rad/s, the gate allows "
                            f"< {MAX_TILT_RATE:g})")
    elif capture_id:
        findings.append(f"capture {capture_id} has no catalog document: nothing recorded its quality")
    else:
        findings.append("no capture is recorded for this commit: it was not made from a scan the indices know about")
    tr = window.get("tilt_rate")
    if tr and tr.get("peak") is not None and tr["peak"] >= MAX_TILT_RATE:
        findings.append(f"telemetry: peak tilt rate {tr['peak']:.3f} rad/s in the {seconds:g} s before the capture")
    odo = window.get("odom_residual")
    if odo and odo.get("peak") is not None:
        if odo["peak"] > 0.05:
            findings.append(f"telemetry: odometry disagreed with the map by {odo['peak'] * 100:.0f} cm just before the capture")
    elif window:
        # `odom_residual` is published by the simulated robot only (docs/10 D50): the real robot's sources
        # do not compute it. Saying so is the point — silence here reads as "the odometry was fine".
        findings.append("odometry was not recorded for this window, so it cannot vouch for the pose "
                        "(the signal is not published by this robot's telemetry source)")
    trustworthy = bool(gate and gate["passed"]) and not any(f.startswith("telemetry") for f in findings)
    return {"commit": sha, "message": (event or {}).get("message"), "capture_id": capture_id, "at": at,
            "gate": gate, "telemetry": window, "findings": findings, "trustworthy": trustworthy,
            "trace": {"id": trace, "url": url}}


def render(d: dict) -> str:
    g = d.get("gate")
    head = f"commit {d['commit'][:7]}" + (f" ({d['message']})" if d.get("message") else "")
    lines = [head]
    if g:
        lines.append(f"  capture {d['capture_id']}: quality gate {'PASSED' if g['passed'] else 'REJECTED'} "
                     f"(skew {g['skew_ms']} ms, limit {g['max_skew_ms']:g}; tilt rate {g['tilt_rate_max']} rad/s, "
                     f"limit {g['max_tilt_rate']:g})")
    peaks = ", ".join(f"{k} {v['peak']:.3f}" for k, v in sorted(d["telemetry"].items()) if v.get("peak") is not None)
    if peaks:
        lines.append(f"  telemetry peaks before the shutter: {peaks}")
    lines += [f"  ! {f}" for f in d["findings"]] or []
    lines.append("  verdict: " + ("this commit's picture of the room is trustworthy" if d["trustworthy"]
                                  else "don't trust this commit's picture of the room blindly"))
    if d["trace"]["id"]:
        lines.append(f"  trace {d['trace']['id']}" + (f"  {d['trace']['url']}" if d["trace"]["url"] else ""))
    return "\n".join(lines)
