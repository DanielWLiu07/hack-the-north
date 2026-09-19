# 03: Interfaces (the contracts every track builds against)

Units: metres, and degrees for room-frame yaw. BB's own values are radians and only appear inside
`frames` / `bb_nav`. **Every pose that leaves the laptop is in the room frame**, marked
`"frame": "world_z_up"` (docs/31 §4).

---

## 1. `roomctl/frames.py`: the only conversion module

```python
@dataclass(frozen=True)
class SE2:                      # T_bb<-room: p_bb = R(theta) p_room + (tx, ty); z_bb = z_room + dz
    theta: float                # rad
    tx: float
    ty: float
    dz: float = 0.0

def room_to_bb(p, T) -> tuple[float, float, float]
def bb_to_room(p, T) -> tuple[float, float, float]
def heading_room_to_bb_yaw(phi_room_deg: float, T) -> float     # h = theta + radians(phi) - pi/2
def bb_yaw_to_heading_room(h: float, T) -> float                # inverse, deg, wrapped to (-180, 180]
def robot_rel_to_bb(X, Y, px, py, h) -> tuple[float, float]     # BB doc: +X right, +Y forward
def bb_to_robot_rel(x, y, px, py, h) -> tuple[float, float]
def base_pose_to_navigate(b: BasePose, T) -> dict                # {"x", "y", "heading", "frame": "world"}
```
**Conventions:**
- **BB world:** at yaw 0 the robot faces +y, +x to its right, and yaw is CCW, so forward is
  (−sin h, cos h).
- **Room frame:** X forward from the tag, Y left, and heading is degrees from +X, CCW.
- A heading angle measured from +x, in BB's frame, is `φ_bb = h + π/2`. So
  `h = θ + φ_room − π/2`.

---

## 2. `roomctl/registration.py`: T_bb←room, per map generation

```python
@dataclass
class Registration:
    T: SE2
    map_gen: int                # BB's map_gen when estimated; invalid once it changes
    source: str                 # "tag" | "teach"
    residual_m: float           # fit quality; refuse to plan above 0.03
    at: str                     # ISO time

def from_tag(tag_in_cam, cam_to_base, robot_pose_bb, tag_pose_room) -> Registration
def from_teach(room_pts: list[tuple[float, float]], bb_pts: list[tuple[float, float]]) -> Registration
def load(repo) -> Registration | None      # .git/gitspace/registration.json (local, never committed)
def save(repo, reg: Registration) -> None
def valid(reg, map_gen: int) -> bool
```
**`from_tag` chains four transforms:** the tag pose in the camera (AprilTag detector) → camera
to base (the fixed mount, measured at Gate 1 **in BB's robot frame**: +x right, +y forward, z up)
→ base to BB world (`rx, ry, rh` from `/ws`, **taken at the frame's timestamp**) → room from tag
(the tag is the room's origin, `room.yaml` anchor).

**`from_teach`** uses ≥ 2 marked points (a 2-point closed form; a least-squares fit for more).

---

## 3. `roomctl/bb_nav.py`: the Bracket Bot nav client

```python
class VoxelMirror:              # ws :8010/heavy
    res: float | None
    cells: dict[tuple[int, int, int], tuple[int, int, int]]   # cell -> rgb
    version: int                # +1 per applied message
    def apply(self, ptype: int, count: int, raw: bytes) -> None    # types 4 and 5; first=1 clears
    def points(self) -> tuple[np.ndarray, np.ndarray]              # (N,3) BB-world metres, (N,3) uint8

@dataclass
class NavState:                 # ws :8010/ws, "t": "state", ~8 Hz
    ready: bool; x: float; y: float; h: float; status: str; running: bool
    waypoints: list; wp: int; goal: tuple[float, float] | None; path: list | None
    map_gen: int; t: float

@dataclass
class AreaMap:                  # GET :8020/map · ws :8020/stream
    anchor_world: dict; bounds: dict
    grid: np.ndarray            # (ny, nx) uint8: 1 floor, 2 obstacle, 0 unknown; row 0 = ymin (area frame)
    resolution_m: float
    freshness: np.ndarray       # (ny, nx) seconds since seen per 0.5 m block; -1 = never
    block_m: float; t: float

@dataclass
class Job:                      # from /health .job
    kind: str; running: bool; error: str | None; result: str | None; progress: dict | None

class BBNav:
    def __init__(self, host: str, ws_port: int = 8010, api_port: int = 8020): ...
    def start(self) -> None                    # background readers; auto-reconnect; keep up (queue of 8)
    state: NavState | None; mirror: VoxelMirror; area: AreaMap | None
    def on_map_reset(self, fn) -> None         # called when map_gen changes or an empty full copy arrives
    def health(self) -> dict; def pose(self) -> dict        # /pose is 503 until SLAM is ready
    def define_area(self, xmin, xmax, ymin, ymax, sweep=True, lane_spacing=0.6, timeout=600) -> Job
    def navigate(self, x, y, heading=None, frame="world", timeout=120) -> Job
    def patrol(self, goal_timeout=90) -> Job
    def stop(self) -> None
    def wait(self, timeout: float) -> Job      # until job.running is False

class BBNavRobot:               # implements roomctl.executor.Robot
    def __init__(self, nav: BBNav, reg_provider, arm=None, voice=None, resume_patrol=True): ...
    def drive(self, base: BasePose) -> None     # frames → navigate → wait → check the ACTUAL pose
    def pose(self) -> BasePose                  # the room frame
    def pick(self, object_id, pose) -> None     # arm adapter, or RobotError("arm_unavailable") in Tier B
    def place(self, object_id, pose, zone) -> None
    def say(self, text) -> None; def led(self, state) -> None     # robot/server.py (HttpRobot._fire)
```
**Error mapping** (RobotError code ← BB):

| BB | RobotError |
|---|---|
| `job.error` = `NavError: …` | `nav_failed` |
| `job.error` = `TimeoutError: …` | `nav_timeout` |
| `job.error` = `cancelled` | `nav_cancelled` |
| `status` = `waiting_for_drive` | `drive_busy` |
| `status` = `manual` | `manual_override` |
| `ready` = false | `slam_not_ready` |
| `map_gen` changed during a job | `map_reset` |
| arrived more than `arrive_tol` (0.25 m) from the target | `nav_short` (the op is skipped honestly, not retried blindly) |

---

## 4. `perception/bb_source.py`: the voxel map becomes objects

```python
@dataclass
class Candidate:
    zone: str
    centroid: tuple[float, float, float]      # room frame
    extents: tuple[float, float, float]       # PCA-aligned footprint + height
    yaw_axis_deg: int                         # [0, 180), the axis of extents.x (state.py rules)
    color: str                                # "#rrggbb", median of the cells
    cells: int
    block: tuple[int, int]                    # its 0.5 m freshness block

def candidates(mirror, reg, zones, min_cells=20, band=(0.005, 0.40)) -> list[Candidate]
    # crop each zone above `surface` + band.min, up to band.max; drop the surface plane; 2D connected
    # components on the (i, j) grid of occupied columns; merge columns into objects
def fresh_blocks(area: AreaMap, reg, max_age_s=10.0) -> set[tuple[int, int]]
def visible(candidate_or_record, grid_room: VoxelGrid, eye_room) -> bool     # raycast.line_of_sight
def scan_into_bb(repo, nav: BBNav, reg, *, segmenter=None, describe=None) -> ScanResult
    # candidates → labels from the latest robot frame → associate (miss only if fresh AND visible)
    # → settle → serialize → voxelize.stage → publish.stage_scan  (the same staging a commit publishes)
```

---

## 5. `roomctl/policy.py`: mess or decision?

```python
Verdict = Literal["mess", "decision", "personal", "untracked_shared", "untracked_personal"]
Action  = Literal["tidy", "chore", "ignore", "lost_and_found"]
def classify(entry, room: dict, head_sha: str, tier: str) -> tuple[Verdict, Action]
```
- A change vs `main` in a `policy: shared` zone → `mess` → `tidy` (Tier A) or `chore` (Tier B).
- A tracked change that is on `main` because a PR merged it → it isn't in `git status` at all.
  That's the point.
- In a `policy: personal` zone → `personal` → `ignore`.
- A new object in a shared zone → `untracked_shared` → `lost_and_found`; in a personal zone →
  `ignore`.

**`room.yaml` v2** (all optional; today's rooms stay valid):
```yaml
zones:
  desk:   {min: [...], max: [...], surface: 0.70, policy: shared}
  my_side: {min: [...], max: [...], surface: 0.70, policy: personal, owner: daniel}
lost_and_found: {pose: [0.30, -0.75, 0.45]}      # alias of `bin`
nav:
  area: {xmin: -1.0, xmax: 1.0, ymin: -0.5, ymax: 1.5}     # BB robot-relative, at definition time
```
⚠ `repo.write_room_files` pins `room.yaml` once, and the demo's room.git lacks `bin`/`home` (D39).
Adding v2 fields there is **one commit to `room.yaml` on main**: the user's call, the same one as D39.

---

## 6. `roomctl/watch.py`: continuous `git status`

```python
@dataclass
class RoomState:
    clean: bool; head: str; branch: str
    confirmed: list[dict]       # {object_id, type, verdict, action, passes, block_age_s}
    pending: list[dict]         # seen once; awaiting the second fresh pass
    stale_blocks: int; at: str

class Watch:
    def __init__(self, repo, nav: BBNav, reg_provider, *, tier="A", debounce_passes=2, fresh_s=10.0,
                 publish=None, heartbeat=None, jobs=None): ...
    def tick(self) -> RoomState       # ~1 Hz: only re-scan blocks that went fresh since the last tick
    def run(self) -> None             # patrol on; heartbeat every 30 s; SSE on change; actions per policy
```
CLI: `room watch [--tier A|B|C] [--no-act]` · `room chores` · `room status --live` (one fresh
pass, then status).

---

## 7. `roomctl/pr.py`: intent as pull requests (git-native)

```python
@dataclass
class PR:
    id: int; branch: str; title: str; author: str; base_sha: str; head_sha: str
    status: str                 # open | merged | closed
    ops: list[dict]             # the preview plan (gitspace.plan/1 ops)

def propose(repo, object_id: str, zone: str, author: str, title: str | None = None) -> PR
    # a free spot in `zone` (executor staging search, clearance kept) → the record moved →
    # a commit on pr/<n>-<slug>, message = title, trailer `Proposed-by: <author>`
def list_prs(repo) -> list[PR]
def approve(repo, pr_id: int, approver: str) -> str
    # merge into main (--no-ff), trailer `Approved-by: <approver>`; returns the merge sha; a tidy job follows
def close(repo, pr_id: int) -> None
```
CLI: `room pr open <object> --to <zone>` · `room pr list` · `room pr approve <n>` · `room pr close <n>`.

---

## 8. Web (dashboard) API

| method · path | body / answer |
|---|---|
| `GET /api/room/ci` | `{state: clean\|dirty\|unknown, since, head, changes, heartbeat: {slug: "room-clean", last: ok\|error, at}}` |
| `GET /api/chores` | `[{id, object_id, zone, verdict, opened_at, status, frame_url}]` |
| `GET /api/prs` · `POST /api/prs` | `[PR]` · `{object_id, zone, title}` → 201 `PR` |
| `POST /api/prs/{id}/approve` | → `{merge_sha, job_id}` (local, or `Authorization: Bearer $GITIRL_CLOUD_TOKEN`) |
| `GET /api/blame/{object_id}` | `{object_id, moved_in: {sha, at, capture_id}, from, to, frame_url}` |
| `GET /api/nav/snapshot` | `{pose: {x, y, yaw}, status, path: [[x, y]…], grid: {res, bounds, cells_b64}, freshness: {block_m, ages}, map_gen, at, frame: "world_z_up"}` (**room frame**, converted on the laptop) |
| `POST /api/edge/event` | **laptop → cloud** push (bearer token): `{event: room_state\|nav\|chore\|pr, data}` |

SSE (`/api/events`), new names: `room_state` · `nav` (≤ 2 Hz) · `chore` · `pr`, plus the existing `job`.

---

## 9. Sentry

| signal | shape |
|---|---|
| **cron `room-clean`** (the CI badge) | interval 1 min, margin 2; `ok` when `RoomState.clean` (after debounce), `error` while a confirmed mess exists. Needs the `watch-loop` seat freed (the user's call) |
| **transaction `room tidy`** | spans `nav.navigate` (target, `arrive_err_m`, job error) → `robot.pick` / `robot.place` → `verify.pass`; tags `capture_id`, `commit_sha`, `map_gen`, `tier` |
| **issues** | `nav_failed` (context: pose, path, goal, BB status) · `nav_short` · `map_reset` (warning) · the existing `grasp_slipped` → self-resolved after a verified pass |
| **breadcrumbs** | BB status transitions, `ready` flips |

## 10. Elasticsearch (no mapping changes)

| index | new documents |
|---|---|
| `room-events` | `event_type`: `chore_opened` · `chore_closed` · `pr_opened` · `pr_merged` · `tidy` with `objects_affected`, `zone`, `message`, `author`, `branch`, `outcome` |
| `room-observations` | `camera: "bb_map"`, `raw_x/y/z` (room frame), `occluded` from the raycast |
| `robot-telemetry` | `signal`: `nav_x`, `nav_y`, `nav_yaw` |
| `queries.py` | `moved_at(object_id)` (blame); `commit_at` gets its production caller (`restore --before`) |

## 11. Feature flags

| var | values | default |
|---|---|---|
| `ROOM_NAV` | `mock` · `http` · `bb` | `mock` (today) |
| `ROOM_SOURCE` | `fake:<scene>` · `perception:<dir>` · `bb` | as today (`ROOM_SCANNER`) |
| `BB_HOST` | the robot's IP / tailnet name | none |
| `ROOM_TIER` | `A` · `B` · `C` | `C` |
| `WATCH_DEBOUNCE` / `WATCH_FRESH_S` | passes / seconds | `2` / `10` |

---

## 12. The split with Andrew (Sat 13:00): **we parse and decide, his layer understands, his edge executes**

```
text ──► OUR grammar (deterministic)  ──match──► Intent (source: "grammar")
              │ no match
              ▼
         ANDREW's intent service (OpenAI, structured output)  ──► Intent (source: "openai")
              │                                                    validated against OUR schema
              ▼
OUR logic: resolve the object (Elastic hybrid search; Andrew's resolver for vague descriptions)
           → policy → plan → ONE complete job ──► Andrew's edge POST /v1/jobs ──► robot
```

**Intent (JSON Schema lives in `bridge/intent.schema.json`, ours; strict, no extra keys):**
```json
{"request_id": "c7f2…", "intent": "find|point|tidy|move|status|blame|restore_time",
 "object_query": "my keys", "object_id": null, "zone": null, "when": null,
 "raw_text": "where did I leave my keys", "confidence": 0.93, "source": "grammar|openai"}
```
| intent | example | our logic | job to the edge |
|---|---|---|---|
| `find` / `point` | "where are my keys?" | resolve → last pose | `point` |
| `tidy` | "clean up the desk" | status → confirmed mess → one job per object | `move` × n (sequential) |
| `move` | "put the lamp on the shelf" | a PR proposal → approval | `move` (after approval) |
| `status` | "is the room clean?" | read-only | none |
| `blame` | "who moved my mug?" | `moved_at` + the capture frame | none |
| `restore_time` | "like it was before dinner" | ES\|QL `commit_at(when)` → restore | `move` × n |

**Jobs to Andrew's edge** (his parser at `9582081`; one action per job):
```json
{"job_id": "job_…", "command": "point", "object_id": "keys_7c2e",
 "target_pose": {"x": 0.62, "y": 0.78, "z": 0.905, "yaw": 140}, "zone": "shelf", "pointing_at": "…"}
{"job_id": "job_…", "command": "move", "target": "<commit sha>",
 "ops": [{"op": "moved", "object_id": "mug_a1b2", "class": "mug", "zone": "desk",
          "from": {"x": 0.61, "y": 0.18, "z": 0.75, "yaw": 40}, "to": {"x": 0.42, "y": 0.18, "z": 0.75, "yaw": 15}}]}
```
All poses are in the room frame (metres, Z-up, yaw in degrees); the robot adapter converts. Job ids are
deterministic per request, so his in-memory cache and our ledger agree.

**Andrew's AI layer:**
- `POST /v1/intent {text, request_id}` → an Intent (OpenAI structured output, schema-validated, and it
  refuses rather than guesses).
- `resolve(object_query) → [(object_id, score)]` for vague descriptions ("the thing I cut paper with").
  It runs on **Elasticsearch as the vector store** (Jina `semantic_text` + BM25 + rerank through
  `elastic/queries.py`) with an LLM tie-break only when the top two are close. **No separate vector
  database:** the Elastic prize story depends on Elastic being the memory.
- Object descriptions (`perception/describe.py`, OpenAI vision) are his; their quality is what the
  vector leg searches.
- **An LLM never produces a job.** Only our logic builds jobs, from a validated Intent.
