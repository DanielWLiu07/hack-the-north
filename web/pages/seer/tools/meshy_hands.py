#!/usr/bin/env python3
"""Generate Seer's cartoon glove hands with Meshy (text-to-3D, PREVIEW meshes only).

Why only the hands. Meshy's rigger works on humanoid bipeds; Seer is a pyramid on noodle arms
(docs/21-3d-and-meshy.md reached the same conclusion for the robot arm). So the pyramid and the
eye are procedural (exact, and the eye is animated), the arms are our own jointed chains, and
Meshy makes the one organic part: the four-fingered glove hands, one static mesh per gesture.
We apply our own materials, so the untextured preview mesh is all we need (no refine step).

    MESHY_API_KEY=... python3 tools/meshy_hands.py [pose ...]

Writes models/hand_<pose>.glb and models/hands.json (task ids, credits, prompts).
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

API = "https://api.meshy.ai/openapi/v2/text-to-3d"
OUT = Path(__file__).resolve().parent.parent / "models"

# shape words first; negate the drift the subject invites; ONE style tail so the set reads as a set
STYLE = ("Cartoon mascot glove hand with exactly four digits: three fat fingers and one thumb. Chunky rounded "
         "sausage fingers, smooth inflated simple shapes like a rubber-hose cartoon glove, matte single colour. "
         "No fingernails, no wrinkles, no knuckle creases, no jewellery, no arm. Ends in a short round wrist stump. "
         "Single object, centred, neutral lighting, no base, no pedestal, no background.")
POSES = {
    # Meshy honours the STYLE reliably and the POSE poorly: the first round returned a peace sign for
    # "pointing", and for "thumbs up" a fist with one finger raised (reads as a rude gesture - rejected).
    # Second-round wording: say which digits are DOWN before which one is up, and name the emoji.
    "point": "A pointing-finger hand like the index-pointing emoji: a closed fist held sideways with ONLY the index finger extended straight out horizontally. The other two fingers are curled tight into the palm and the thumb is tucked down over them. Exactly one finger is extended.",
    "thumbsup": "A thumbs-up hand like the thumbs-up 'like' emoji: a closed fist held sideways with all three fingers curled tight into the palm, and ONLY the short fat thumb sticking straight up. No finger is raised, only the thumb.",
    "open": "An OPEN relaxed hand, palm facing down, fingers slightly spread and gently curved, as if resting on a keyboard.",
    "grip": "A closed fist: all three fingers curled tight into the palm with the thumb wrapped across the front of them, like a hand holding a handle. No finger is extended.",
    "peace": "A PEACE SIGN hand: two fingers raised in a V, the third finger and the thumb folded into the palm.",
}


def call(method: str, url: str, key: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(url, method=method, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                                 data=json.dumps(body).encode() if body else None)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main() -> None:
    key = os.environ.get("MESHY_API_KEY", "").strip()
    if not key:
        raise SystemExit("MESHY_API_KEY is not set")
    poses = sys.argv[1:] or list(POSES)
    OUT.mkdir(exist_ok=True)
    log_path = OUT / "hands.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else {}
    tasks = {}
    for pose in poses:
        if (OUT / f"hand_{pose}.glb").exists():
            print(f"{pose}: already generated, skipping"); continue
        prompt = f"{POSES[pose]} {STYLE}"
        task = call("POST", API, key, {"mode": "preview", "prompt": prompt, "ai_model": "latest", "topology": "triangle",
                                        "target_polycount": 9000, "should_remesh": True, "target_formats": ["glb"]})["result"]
        tasks[pose] = task
        log[pose] = {"task_id": task, "prompt": prompt}
        print(f"{pose}: submitted {task}", flush=True)
    deadline = time.time() + 25 * 60
    while tasks and time.time() < deadline:
        time.sleep(10)
        for pose, task in list(tasks.items()):
            st = call("GET", f"{API}/{task}", key)
            if st["status"] == "SUCCEEDED":
                urllib.request.urlretrieve(st["model_urls"]["glb"], OUT / f"hand_{pose}.glb")
                log[pose].update(status="SUCCEEDED", consumed_credits=st.get("consumed_credits"),
                                 bytes=(OUT / f"hand_{pose}.glb").stat().st_size)
                print(f"{pose}: done, {log[pose]['bytes'] // 1024} KB, credits {st.get('consumed_credits')}", flush=True)
                del tasks[pose]
            elif st["status"] in ("FAILED", "CANCELED"):
                log[pose].update(status=st["status"], error=str(st.get("task_error"))[:200])
                print(f"{pose}: {st['status']} {st.get('task_error')}", flush=True)
                del tasks[pose]
    for pose in tasks:
        log[pose]["status"] = "TIMEOUT"; print(f"{pose}: still running after 25 min")
    log_path.write_text(json.dumps(log, indent=1))


if __name__ == "__main__":
    main()
