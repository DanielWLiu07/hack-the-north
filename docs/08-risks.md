# 08 — Risk register

Ordered by expected damage. Each has a mitigation and, where it matters, a **kill
criterion** — a time by which, if it isn't working, we stop and take the fallback.

---

### R1 — Hackathon wifi kills the Pi↔laptop link
**Likelihood: high. Damage: total.** Rerun streams over the network, the Pi is headless,
and 1000+ people are on that wifi.
**Mitigation:** bring our **own travel router / AP**, or better, ethernet + a small switch.
Set static IPs. Test the whole loop on our own network before anything else. Also: make the
system work with the laptop *tethered directly* to the Pi as a last resort.
**This is the highest-probability project-killer and the cheapest one to prevent. Do it first.**

---

### R2 — Arm motion destabilizes the balancing robot
**Likelihood: high. Damage: high** (falls break hardware and morale).
**Mitigation:** slow trapezoidal arm profiles; low, axis-adjacent arm mount; manipulate
only from a stop; consider a fold-down foot or resting the chassis against the table edge.
See [`02-hardware.md`](02-hardware.md#the-balance-problem-read-this-before-mounting-anything).
**Kill criterion:** if by **hour 18** the arm can't complete a pick without a fall, switch
to either (a) manipulation only while braced against the table, or (b) a separate
fixed-base SO-101 doing the manipulation while Bracket Bot navigates and perceives.

---

### R3 — Stereo depth fails on textureless surfaces
**Likelihood: high. Damage: medium-high.** SGBM produces nothing on a plain white table,
blank walls, or glossy/transparent objects. Whole regions of the cloud come back empty and
objects silently vanish → phantom "deleted" diffs.
**Mitigation:** textured tablecloth; curated matte, patterned objects; tune SGBM on the
*actual* table Friday night; use the VL53L5CX ToF as a sanity check on close range;
get a RealSense if one exists in the building. Log per-object point counts so we can *see*
when depth is thin rather than guessing.

---

### R4 — Phantom diffs from noise and drift
**Likelihood: high. Damage: high** (the system becomes a liar, which is worse than broken).

**Update:** Bracket Bot ships SLAM/nav daemons. If their SLAM provides a persistent global
pose, **the drift half of this risk drops substantially** — see
[`robot/README.md`](../robot/README.md). Confirm at the booth before relying on it, and keep
one anchor tag up regardless as an independent drift check. The *sensor-noise* half of this
risk is unchanged and still needs quantization and hysteresis.
**Mitigation:** the whole first section of [`03-perception.md`](03-perception.md#the-real-enemy-phantom-diffs)
— quantization, hysteresis, stable IDs, confidence gating, AprilTag anchor.
**Build `test_idempotent_scan.py` in the first few hours** (scan twice → `git diff
--exit-code` must be clean) and treat a red result as a stop-the-line event.

---

### R5 — Grasping doesn't work reliably enough to demo
**Likelihood: medium-high. Damage: medium** (fallback ladder is good).
**Mitigation:** curated object set, top-down grasps only, generous gripper-friendly shapes,
place tolerance of several cm. Don't attempt general grasping.
**Kill criterion:** by **hour 24**, if a pick-and-place isn't succeeding ~70% of the time,
demote to fallback rung 2 ([`06-demo.md`](06-demo.md#fallback-ladder)) and spend the
remaining time on `status`/`diff`/`blame` polish and the Rerun screen.

---

### R6 — Three-camera calibration eats a day
**Likelihood: medium. Damage: medium.**
**Mitigation:** T2 is deliberately *after* T0 and T1 — the single stock camera must carry
a complete demo first. Timebox calibration to **2 hours**. If the extrinsics don't converge,
ship with one camera and re-frame: "we scan by rotating; 360° is the next step."
Nobody will dock us for it if the core loop sings.

---

### R7 — USB bandwidth / camera enumeration chaos
**Likelihood: medium. Damage: medium.** Three MJPG stereo streams on one Pi; `/dev/video*`
indices also shuffle between boots.
**Mitigation:** **round-robin capture** (open→grab→close) removes the bandwidth problem
entirely. Bind cameras by **stable udev path** (`/dev/v4l/by-path/...`), never by integer
index — an index shuffle at 4am looks exactly like a broken camera and wastes an hour.

---

### R8 — Occlusion read as deletion
**Likelihood: certain. Damage: medium.**
**Mitigation:** `unobserved` state carried forward from HEAD rather than deleted;
360° coverage; optional second vantage point before declaring a deletion. Turn this from
a bug into a talking point — "honest handling of ambiguity" is what the serious prize
briefs ask for.

---

### R9 — Scope creep from prize chasing
**Likelihood: high. Damage: high.**
**Mitigation:** [`07-prizes.md`](07-prizes.md). Lock the prize list Saturday noon and
stop adding SDKs. **Nothing new gets started after hour 28** — that block is for
rehearsal, recording, the Devpost writeup, and sleep.

---

### R10 — A camera gets bumped after calibration
**Likelihood: medium. Damage: high and silent** — all three clouds misalign and every
commit becomes garbage, with no error message.
**Mitigation:** mechanical rigidity, gaffer tape, and a **calibration health check**: log
the ICP residual between overlapping cloud regions on every capture and alarm if it jumps.
Cheap to add, saves an hour of blind debugging.

---

### R11 — Nobody sleeps and the demo is delivered by zombies
**Likelihood: high. Damage: underrated.** The judging pitch is a live performance.
**Mitigation:** staggered sleep, non-negotiable. The person delivering the pitch gets the
most sleep. Rehearse Saturday night while still coherent.

---

## The one-sentence summary
The technical risks are all survivable with fallbacks; **the wifi and the prize-chasing are
the two that quietly kill the project**, and both are prevented by decisions rather than by work.
