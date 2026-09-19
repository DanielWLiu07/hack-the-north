# 03 — Perception: world → scene graph

The job: turn a shutter click into a **stable, deterministic, human-readable description
of what is in the room and where**, such that taking two snapshots of an unchanged room
produces **byte-identical files**.

That last clause is the whole difficulty. Everything below is in service of it.

## The real enemy: phantom diffs

If a commit of an unchanged room produces even 2 mm of jitter in every object pose,
`git diff` shows fifty changed lines and the demo is dead. The failure is not dramatic —
the robot doesn't crash, it just becomes a liar. Guard against it from hour one:

- **Quantize aggressively before serialization.** Positions to **1 cm**, yaw to **5°**,
  dimensions to 1 cm. We are diffing *human-meaningful* changes, not metrology. Nobody
  moved a cup by 4 mm on purpose.
- **Hysteresis / deadband on top of quantization.** Only write a new pose if it differs
  from the committed pose by more than ~1.5× the quantum. Prevents an object sitting
  exactly on a quantization boundary from flickering between two values every scan.
- **Stable object IDs.** An object's filename must not change because the cluster order
  changed. ID = short hash of `(class_label, first_seen_commit, ordinal)`, assigned at
  first sight and then *carried forward by matching*, never recomputed from geometry.
- **Deterministic ordering.** Sort object files by ID; sort keys in each YAML. Never emit
  a dict in hash order.
- **Confidence gating.** An object seen in only one of N frames of a capture burst is not
  committed. Take 3–5 frames per camera per capture, require an object to appear in the
  majority.
- **Build the "scan twice, expect empty diff" test in the first hours** and run it
  constantly. `git diff --exit-code` after a no-op rescan is our regression suite. If it
  isn't green, nothing else matters.

**This is also our best story for the Rox prize** — messy, noisy, contradictory real-world
sensor data resolved into stable entities that an agent then acts on. It's not decoration,
it's the actual hard part. Say so in the pitch.

## Pipeline

```
 [capture]   3 stereo pairs, round-robin, 3-5 frames each
     |       (existing: lib/camera.py StereoCamera.get_stereo)
     v
 [depth]     fisheye rectify -> StereoSGBM -> reprojectImageTo3D -> per-cam cloud
     |       (existing: examples/example_depth.py — fork this, don't rewrite)
     v
 [fuse]      apply per-cam extrinsics -> single cloud in ROBOT frame
     |       -> apply robot pose (AprilTag anchor + odometry) -> WORLD frame
     v
 [clean]     voxel downsample (1 cm) -> statistical outlier removal
     |       -> RANSAC plane fit: remove floor + table top (keep the plane as a "zone")
     v
 [segment]   Euclidean cluster extraction -> candidate object clusters
     |       -> reject by size/point-count priors
     v
 [label]     crop each cluster's source image region -> open-vocab label
     |       (YOLO-world / SAM 3 / GPT-5 vision — see "labeling" below)
     v
 [describe]  per object: class, oriented bbox (centroid + extents + yaw), dominant
     |       colour, point count, confidence, zone assignment
     v
 [associate] match against HEAD's objects (Hungarian) -> carry IDs forward
     v
 [serialize] quantize -> deterministic YAML -> zones/<zone>/<id>.yaml
     v
 [git]       git add / status / diff / commit  (real git, see 04)
```

## Localization: the anchor

Diffs are only meaningful if every scan lands in the same coordinate frame.
Wheel odometry alone drifts; over a 3-minute demo with several drives, it will drift
enough to fake object movement.

**Plan: an AprilTag on the wall is the repo origin.** Every capture that sees the anchor
tag re-zeroes the robot pose (`solvePnP` on the tag corners). Between anchor sightings we
integrate the existing `DifferentialDriveOdometry`. Refinement: ICP the new cloud's
static structure (walls, table plane) against the committed cloud to snap it into place.

- Put the anchor tag where the 360° rig can basically always see it.
- If we can get **multiple** tags up, do it — more constraints, less drift, and it costs
  nothing but printer paper.
- Log the anchor's re-localization residual to Rerun. If it spikes, we know instantly why
  a diff looks wrong, at 4am, instead of guessing.

## Labeling: three options, pick by what's working

1. **YOLO / segmentation examples already in the quickstart** (`example_yolo.py`,
   `example_segmentation.py`). Fastest to stand up, fixed label set, runs on the Pi.
   **Start here.**
2. **SAM 3** — promptable *concept* segmentation: open-vocabulary noun-phrase prompts,
   segments and tracks every instance of a concept. Exactly right for "find all the cups"
   and for a `.roomignore` that says "ignore people". Needs to run off-board.
3. **GPT-5 vision on cluster crops** — slowest, most flexible, best labels, and it gives
   us the OpenAI-prize story plus a natural place for genuine agent reasoning ("these two
   clusters are one object seen from two sides").

Realistic plan: **(1) for the pipeline to work at all, (3) layered on for quality and for
the agent story, (2) if there's time.** Run the heavy ones on a laptop GPU or **Baseten**
(that's the Baseten prize, and it's genuinely the right call — the Pi should not be doing
this).

## Object association between commits

Given HEAD's object set and the fresh scan's object set, build a cost matrix and solve
with Hungarian assignment. Cost terms:

- centroid distance (dominant term, gated: beyond ~1.5 m it's never the same object)
- class-label agreement (or embedding cosine distance if we use CLIP-style features)
- bbox extent similarity
- dominant-colour histogram distance

Then:

| outcome | meaning | git surface |
|---|---|---|
| matched, pose delta < threshold | unchanged | (no diff) |
| matched, pose delta > threshold | **moved** | modified file, pose lines change |
| in HEAD, unmatched | **removed** | deleted file |
| in scan, unmatched | **added** | new file |
| matched, class changed | reclassified | modified file, `class:` line changes |

The moved/added/removed trichotomy is exactly git's, which is not a coincidence — it's
why the metaphor holds up under load.

**Known hard case:** an object that is *occluded* rather than removed. From one viewpoint
"gone" and "hidden behind the laptop" look identical. Mitigations: the 360° rig sees more;
mark objects in currently-occluded volumes as `unobserved` rather than deleting them
(carry them forward from HEAD untouched); optionally have the robot drive to a second
vantage point before declaring a deletion. **Handling this well is a genuinely impressive
thing to mention to a judge** — it's "honest handling of ambiguity," which is what the
serious prizes (Rox, RBC) say they reward.

## Zones

A **zone is a directory**. RANSAC-detected support surfaces (table tops, shelf planes)
plus manually-named regions become `zones/desk/`, `zones/shelf/`, `zones/floor/`.
Objects are filed under the zone whose support plane they rest on.

This is what makes `git add desk/`, `git log -- zones/shelf/`, and a `.roomignore`
that ignores `zones/floor/` work. See [`04-git-semantics.md`](04-git-semantics.md).

## People are never committed

Hard rule, for obvious reasons and because a judge may well ask: humans are detected and
**excluded** from the scene graph and from any stored imagery. `.roomignore` ships with
`person` in it by default. It costs us nothing and it's the right call. Say it out loud
in the demo — it lands well.
