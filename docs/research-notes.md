# Research notes

Gathered 2026-09-17. Everything here is to (a) avoid reinventing solved things and
(b) know what our originality claim actually is.

## Prior art, and how we differ

**Scene versioning in virtual 3D** — the metaphor has been explored, but only for digital
scenes edited with a mouse:
- **SceneGit** — tracks object-level changes in a 3D scene, down to vertex/face granularity.
- **MeshGit** — a mesh edit distance for diffing polygonal meshes in modeling workflows.
- **"Who Put That There"** — records virtual objects' spatial trajectories from direct
  manipulation in VR.

**Robot change detection in the real world** — mature and active, but it stops at *detecting*:
- [**SceneDiff**](https://arxiv.org/abs/2512.16908) (2026) — first multiview benchmark for
  object-level change detection across different camera trajectories; 350 video pairs.
  Training-free method: co-register with pretrained 3D/segmentation/appearance models, then
  find geometric + semantic inconsistencies at object level. **Closest thing to our
  perception stage; read the method section.**
- [**POCD**](https://arxiv.org/pdf/2205.01202) — probabilistic object-level change detection
  and volumetric mapping in semi-static scenes.
- [**OASIS-Map**](https://arxiv.org/html/2607.14899) — object-level change detection in
  multi-session mapping via semantic correspondence matching. The "robot revisits a room
  that changed while it was away" framing is exactly ours.
- [**3D VSG**](https://arxiv.org/abs/2209.07896) — variable scene graphs; change *prediction*
  as well as detection.
- [**DSG**](https://arxiv.org/html/2609.00619) (2026) — dynamic 3D scene graph construction
  for embodied agents in changing indoor environments; dual-view rendering-based change
  detection to identify missing objects.
- [**Objects Can Move**](https://export.arxiv.org/pdf/2306.15416) — 3D change detection via
  geometric transformation consistency; learned descriptors + RANSAC to recover the rigid
  transform of each moved object. Directly relevant to reporting *how* an object moved.

**Our gap:** every one of these detects change. None of them (a) serialize the scene into
a real git repository with real git plumbing, (b) expose the full git verb surface —
branch, merge, conflict, blame, cherry-pick, push, PR — over physical space, or (c) close
the loop with a **physical executor that applies a diff back onto the world**. The novelty
is the round trip and the fact that the middle of the system is literally `git`.

## Perception building blocks

**Point cloud change detection (classical, fast, good enough):**
- [PCL octree spatial change detection](https://pointclouds.org/documentation/tutorials/octree_change.html)
  — recursively compare octree structures across two unorganized clouds of differing size,
  resolution, density and ordering. The cheap baseline.
- [Open3D issue #1853](https://github.com/isl-org/Open3D/issues/1853) — Open3D has no
  built-in equivalent; people use nearest-neighbour distance per point. Note if we're on Open3D.

**Feed-forward 3D from images**, if stereo SGBM disappoints:
- [MASt3R-SLAM](https://edexheim.github.io/mast3r-slam/) — real-time dense SLAM on 3D
  reconstruction priors, globally consistent poses + dense geometry at ~15 FPS.
- [VGGT](https://arxiv.org/abs/2503.11651) (CVPR 2025 best paper) and VGGT-SLAM /
  VGGT-SLAM 2.0 ([GTSAM writeup, 2026](https://gtsam.org/2026/06/24/vggt-slam.html)) —
  feed-forward multi-view geometry; most compute moved to training time.
- **Caveat:** these need a GPU and are a whole integration. Our commits are snapshots, so
  classical stereo is probably sufficient. File under "if SGBM fails and someone has a
  laptop GPU free."

**Monocular metric depth**, as a fallback or to densify:
- [UniDepth](https://openaccess.thecvf.com/content/CVPR2024/papers/Piccinelli_UniDepth_Universal_Monocular_Metric_Depth_Estimation_CVPR_2024_paper.pdf)
  — predicts metric 3D points from a single image *without* known intrinsics.
- Depth Anything v2 / v3 — strong relative depth, needs scaling to metric.
- Useful trick: our stereo gives metric scale, monocular gives dense structure. Scale a
  monocular prediction to the stereo points to fill textureless holes. **Cheap and effective
  if R3 bites.**

**Segmentation / labeling:**
- [SAM 3](https://arxiv.org/html/2511.16719v1) — promptable *concept* segmentation: text
  noun-phrase or image-exemplar prompts, finds and segments **every** instance of a concept
  in images and video. [SAM 3.1](https://ai.meta.com/blog/segment-anything-model-3/) adds
  Object Multiplex for faster multi-object real-time tracking. Ideal for `.roomignore`
  ("ignore person") and for open-vocabulary object labels.
- The BB quickstart already ships YOLO and segmentation examples — start there.

**6-DoF pose and grasping**, if we get ambitious:
- [FoundationPose](https://nvlabs.github.io/FoundationPose/) — unified 6D pose estimation
  and tracking of novel objects, model-based *or* model-free (a few reference images).
  Would give us exact object orientation for diffs and precise place targets.
- [AnyGrasp SDK](https://github.com/graspnet/anygrasp_sdk) — 6-DoF grasp detection from
  RGB-D/point clouds. **Licence restricts deployment to registered machines** — check before
  relying on it at a hackathon. GraspGen and other GraspNet-family models are alternatives.
- **Realistic call:** both are probably too heavy for 36 h. Curated objects + top-down
  grasps. Mention these as "what productionizing looks like" if a judge asks about generality.

## Calibration

- AprilTag / ChArUco is the standard for multi-camera extrinsics; single-frame solves exist
  and automated rig pipelines recalibrate in well under 15 minutes.
- [CALICO](https://arxiv.org/html/1903.06811v3) — multi-camera calibration with pattern rigs,
  including **non-overlapping** cameras. Relevant if our ±120° pairs don't overlap enough.
- [Incremental multi-camera extrinsic calibration via weighted AprilTag detections + multi-view
  triangulation](https://doi.org/10.3390/a19050371) — tested on 360°-style cylindrical rigs.

## Balancing + manipulation

- [Online CoM estimation for a wheeled-inverted-pendulum humanoid](https://arxiv.org/html/1810.03076)
  — the CoM ground projection must stay in the support polygon; good framing of why the arm
  destabilizes us.
- ETH's approach: map the arm's CoM position/velocity/**acceleration** into predicted body
  perturbations and feed forward into the body trajectory. This confirms the practical
  mitigation — **acceleration is the enemy, so move the arm slowly.**
- [Friction feedforward LQR for wheel-legged balance](https://www.mdpi.com/1424-8220/25/4/1056)
  — feedforward on top of LQR is the standard pattern; BB already runs LQR (`lib/lqr.py`).

## Tools

- [Rerun](https://rerun.io/) — "the data layer for physical AI"; time-aware multimodal viz,
  [blueprints](https://rerun.io/docs/concepts/blueprints) are savable/shareable viewer
  layouts built on the same ECS as recordings. **Already the BB house visualizer.**
  This is our demo screen; design it deliberately.
- [BracketBotCapstone/quickstart](https://github.com/BracketBotCapstone/quickstart) — the
  real spec for the robot. See [`02-hardware.md`](02-hardware.md).
- [LeRobot SO-101 docs](https://huggingface.co/docs/lerobot/so101) — native support,
  calibration, teleop, data collection.

## Sources

- [SceneDiff](https://arxiv.org/abs/2512.16908) · [3D VSG](https://arxiv.org/abs/2209.07896) · [POCD](https://arxiv.org/pdf/2205.01202) · [OASIS-Map](https://arxiv.org/html/2607.14899) · [DSG](https://arxiv.org/html/2609.00619) · [Objects Can Move](https://export.arxiv.org/pdf/2306.15416)
- [PCL octree change detection](https://pointclouds.org/documentation/tutorials/octree_change.html) · [Open3D #1853](https://github.com/isl-org/Open3D/issues/1853)
- [MASt3R-SLAM](https://edexheim.github.io/mast3r-slam/) · [VGGT-SLAM / GTSAM](https://gtsam.org/2026/06/24/vggt-slam.html) · [UniDepth](https://openaccess.thecvf.com/content/CVPR2024/papers/Piccinelli_UniDepth_Universal_Monocular_Metric_Depth_Estimation_CVPR_2024_paper.pdf)
- [SAM 3](https://arxiv.org/html/2511.16719v1) · [SAM 3.1](https://ai.meta.com/blog/segment-anything-model-3/)
- [FoundationPose](https://nvlabs.github.io/FoundationPose/) · [AnyGrasp SDK](https://github.com/graspnet/anygrasp_sdk)
- [CALICO](https://arxiv.org/html/1903.06811v3) · [Incremental multi-cam extrinsics](https://doi.org/10.3390/a19050371)
- [WIP CoM estimation](https://arxiv.org/html/1810.03076) · [Friction feedforward LQR](https://www.mdpi.com/1424-8220/25/4/1056)
- [Rerun](https://rerun.io/) · [Rerun blueprints](https://rerun.io/docs/concepts/blueprints) · [BB quickstart](https://github.com/BracketBotCapstone/quickstart) · [LeRobot SO-101](https://huggingface.co/docs/lerobot/so101) · [Bracket Bot on CBC](https://www.cbc.ca/news/canada/kitchener-waterloo/bracket-bot-customizable-robot-kit-affordability-1.7464018)
