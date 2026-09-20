"""perception/bb_source.py: Bracket Bot's colour voxel map -> the object records serialize writes.

The input is SYNTHETIC and built the way bbsim builds it: fake/scene_gen's scene surfaces,
coloured, voxelized at BB's 1.5 cm in a BB world frame offset from the room frame by a known,
deliberately large T (roomctl.frames). Every assertion is in the ROOM frame, in metres. A
candidate that comes back in BB coordinates is off by a metre, not a millimetre, so the
tolerances below double as the frame test.
"""
import json
import math
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bb_source  # noqa: E402  (puts the repo root on sys.path)
import costmap  # noqa: E402
import voxelize  # noqa: E402
from fake.scene_gen import load_scene, scene_cloud  # noqa: E402
from roomctl import frames  # noqa: E402
from roomctl import repo as roomrepo  # noqa: E402
from roomctl.state import Extents, ObjectRecord, Pose  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
RES = 0.015                                             # BB's /heavy cell, metres
TABLE = "#8b6b4a"                                       # the zone surfaces' colour
T = frames.SE2(theta=0.7, tx=1.3, ty=-0.4, dz=0.05)     # T_bb<-room, far from identity on purpose
REG = SimpleNamespace(T=T, map_gen=1)


def records(scene) -> list[ObjectRecord]:
    return [ObjectRecord(t.id, t.cls, t.zone, Pose(t.x, t.y, t.z, t.yaw), Extents(t.ex, t.ey, t.ez),
                         t.color, "2026-09-19T00:00:00Z") for t in scene.objects.values()]


def rgb(hexc: str) -> tuple[int, int, int]:
    return tuple(int(hexc[i:i + 2], 16) for i in (1, 3, 5))


class Mirror:
    """roomctl.bb_nav.VoxelMirror's read side: res, cells {(i,j,k): (r,g,b)}, points()."""

    def __init__(self, res, cells):
        self.res, self.cells, self.version = res, cells, 1

    def points(self):
        ijk = np.array(list(self.cells), float).reshape(-1, 3)
        return (ijk + 0.5) * self.res, np.array(list(self.cells.values()), np.uint8).reshape(-1, 3)


def box_points(r, spacing: float) -> np.ndarray:
    """An object's box surface, ROTATED by its yaw, without its underside: no camera sees that,
    and cells a map can't have shouldn't hold a test up. (scene_gen.scene_cloud draws each
    object's axis-aligned bounding box, which is right for a costmap but carries no yaw.)"""
    e = (r.extents.x, r.extents.y, r.extents.z)
    ax = [np.linspace(-v / 2, v / 2, max(2, math.ceil(v / spacing) + 1)) for v in e]
    faces = []
    for a in range(3):
        for v in ((e[a] / 2,) if a == 2 else (-e[a] / 2, e[a] / 2)):
            g = np.meshgrid(*[ax[b] if b != a else np.array([v]) for b in range(3)], indexing="ij")
            faces.append(np.stack([m.ravel() for m in g], axis=1))
    p = np.concatenate(faces)
    c, s = math.cos(math.radians(r.pose.yaw)), math.sin(math.radians(r.pose.yaw))
    p[:, :2] = p[:, :2] @ np.array([[c, s], [-s, c]])
    return p + (r.pose.x, r.pose.y, r.pose.z)


def mirror(room, recs, T=T, res=RES, drop=0.0, rgb_noise=0, strays=0, seed=0) -> Mirror:
    """What bbsim serves: surfaces -> BB world (via frames) -> 1.5 cm cells, the objects' colour
    winning the cells they share with the surface they stand on. drop / rgb_noise / strays are
    one re-observation's worth of change, for the G2-across-passes test."""
    rng = np.random.default_rng(seed)
    parts = [(scene_cloud(room, [], spacing=res / 2), TABLE)] + [(box_points(r, res / 2), r.color) for r in recs]
    cells = {}
    for pts, colour in parts:
        idx = np.floor(frames.room_to_bb_array(pts, T) / res).astype(int)
        keep = rng.random(len(np.unique(idx, axis=0))) >= drop if colour != TABLE else None
        for n, c in enumerate(map(tuple, np.unique(idx, axis=0))):
            if keep is None or keep[n]:
                cells[c] = rgb(colour)
    if rgb_noise:
        cells = {c: tuple(int(np.clip(v + rng.integers(-rgb_noise, rgb_noise + 1), 0, 255)) for v in col)
                 for c, col in cells.items()}
    for _ in range(strays):                         # lone speckle cells just above the desk
        p = frames.room_to_bb((rng.uniform(0.15, 0.95), rng.uniform(-0.45, 0.45), 0.75 + rng.uniform(0, 0.2)), T)
        cells[tuple(int(math.floor(v / res)) for v in p)] = (200, 200, 200)
    return Mirror(res, cells)


def match(cands, recs):
    """Each record -> the candidate nearest its centre (xy), each candidate used once."""
    left, out = list(cands), {}
    for r in recs:
        c = min(left, key=lambda c: math.dist(c.centroid[:2], (r.pose.x, r.pose.y)))
        left.remove(c)
        out[r.id] = c
    return out


def axis_err(a: float, b: float, period: float) -> float:
    d = (a - b) % period
    return min(d, period - d)


# ── 1 · candidates ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", ["clean_bench", "messy_bench"])
def test_candidates_are_the_scene_in_the_room_frame(name):
    """docs/roommate 04: count, centroid <= 1 cm, extents <= 1.5 cm, yaw axis +-5 deg, colour."""
    scene = load_scene(name)
    recs = records(scene)
    cands = bb_source.candidates(mirror(scene.room, recs), REG, scene.room["zones"])
    assert len(cands) == len(recs), sorted((c.zone, [round(v, 2) for v in c.centroid]) for c in cands)
    for rid, c in match(cands, recs).items():
        r = next(x for x in recs if x.id == rid)
        assert c.zone == r.zone, rid
        assert math.dist(c.centroid[:2], (r.pose.x, r.pose.y)) <= 0.010, (rid, c.centroid)
        assert abs(c.centroid[2] - r.pose.z) <= 0.010, (rid, c.centroid)
        if r.extents.x / r.extents.y >= 1.3:                 # a real long axis: yaw means something
            assert axis_err(c.yaw_axis_deg, r.pose.yaw, 180) <= 5, (rid, c.yaw_axis_deg, r.pose.yaw)
            got, want = c.extents, (r.extents.x, r.extents.y, r.extents.z)
        else:                                                # near-round: compare the sides, not their order
            got = (*sorted(c.extents[:2]), c.extents[2])
            want = (*sorted((r.extents.x, r.extents.y)), r.extents.z)
        assert all(abs(g - w) <= 0.015 for g, w in zip(got, want)), (rid, got, want)
        assert c.color == r.color and c.cells >= 20 and 0 <= c.yaw_axis_deg < 180


def test_frame_candidates_do_not_depend_on_where_bb_put_its_origin():
    """The same room through two different T_bb<-room: the same candidates, in the room frame."""
    scene = load_scene("clean_bench")
    recs = records(scene)
    a = bb_source.candidates(mirror(scene.room, recs, T=frames.SE2.identity()),
                             SimpleNamespace(T=frames.SE2.identity()), scene.room["zones"])
    b = bb_source.candidates(mirror(scene.room, recs), REG, scene.room["zones"])
    pa, pb = match(a, recs), match(b, recs)
    for rid in pa:
        assert math.dist(pa[rid].centroid, pb[rid].centroid) <= 0.012, rid


def test_units_the_mirror_is_metres():
    """res is metres (0.015), not millimetres or cells: a mirror claiming 15 is refused."""
    scene = load_scene("clean_bench")
    m = mirror(scene.room, records(scene))
    m.res = 15.0
    with pytest.raises(ValueError, match="metres"):
        bb_source.candidates(m, REG, scene.room["zones"])


def test_the_surface_plane_is_dropped_and_nothing_is_invented():
    """An empty desk and shelf -> no candidates: neither the surface nor its edges is an object."""
    scene = load_scene("clean_bench")
    assert bb_source.candidates(mirror(scene.room, []), REG, scene.room["zones"]) == []


def test_lone_speckle_is_not_an_object():
    scene = load_scene("clean_bench")
    recs = records(scene)
    assert len(bb_source.candidates(mirror(scene.room, recs, strays=15, seed=3), REG,
                                    scene.room["zones"])) == len(recs)


def _two_boxes(colour_b: str, height_b: float):
    scene = load_scene("clean_bench")
    a = ObjectRecord("box_a", "box", "desk", Pose(0.40, 0.00, 0.70 + 0.06, 0), Extents(0.10, 0.10, 0.12),
                     "#2b4c7e", "2026-09-19T00:00:00Z")
    b = ObjectRecord("box_b", "box", "desk", Pose(0.40, 0.10, 0.70 + height_b / 2, 0), Extents(0.10, 0.10, height_b),
                     colour_b, "2026-09-19T00:00:00Z")
    return bb_source.candidates(mirror(scene.room, [a, b]), REG, scene.room["zones"])


def test_touching_objects_split_by_colour():
    cands = _two_boxes("#e67e22", 0.12)              # side by side, same height, blue vs orange
    assert len(cands) == 2 and {c.color for c in cands} == {"#2b4c7e", "#e67e22"}


def test_touching_objects_split_by_height():
    cands = _two_boxes("#2b4c7e", 0.04)              # same blue, 12 cm next to 4 cm
    assert len(cands) == 2 and sorted(round(c.extents[2], 2) for c in cands) == pytest.approx([0.04, 0.12], abs=0.015)


# ── 2 · freshness and line of sight ───────────────────────────────────────────────────

def area(anchor, age_s, bounds=(-1.0, 1.0, -0.5, 1.5), block_m=0.5, grid=None, res=0.03):
    xmin, xmax, ymin, ymax = bounds
    g = grid if grid is not None else np.ones((round((ymax - ymin) / res), round((xmax - xmin) / res)), np.uint8)
    return SimpleNamespace(anchor_world=dict(zip(("x", "y", "yaw"), anchor)),
                           bounds={"xmin": xmin, "xmax": xmax, "ymin": ymin, "ymax": ymax},
                           grid=g, resolution_m=res, freshness=np.asarray(age_s, float), block_m=block_m, t=0.0)


def test_fresh_blocks_are_seen_recently_and_never_the_never_seen():
    a = area((0.0, 0.0, 0.0), [[3.0, 20.0], [-1.0, 8.0]])
    assert bb_source.fresh_blocks(a, REG, max_age_s=10.0) == {(0, 0), (1, 1)}     # (col, row)


def test_fresh_blocks_reads_the_raw_map_payload_too():
    """GET /map's own shape, base64 grid and all, as bbsim and bbapps/nav serve it."""
    import base64
    raw = {"area": {"anchor_world": {"x": 0.0, "y": 0.0, "yaw": 0.0},
                    "bounds": {"xmin": -1.0, "xmax": 1.0, "ymin": -0.5, "ymax": 1.5}},
           "grid": {"resolution_m": 0.03, "nx": 2, "ny": 1, "cells": base64.b64encode(bytes([1, 2])).decode()},
           "freshness": {"block_m": 0.5, "nx": 2, "ny": 2, "age_s": [[3.0, 20.0], [-1.0, 8.0]]}, "t": 0.0}
    assert bb_source.fresh_blocks(raw, REG) == {(0, 0), (1, 1)}


@pytest.mark.parametrize("yaw,want", [(0.0, (2, 2)), (math.pi / 2, (3, 0))])
def test_block_of_a_room_point_by_hand(yaw, want):
    """T = identity, anchor at BB's origin. Yaw 0: area X = x, Y = y. Yaw pi/2 (facing -x):
    +X right = +y, so X = y, Y = -x. Room (0.26, 0.74) with xmin -1, ymin -0.5, 0.5 m blocks."""
    a = area((0.0, 0.0, yaw), [[0.0] * 4] * 4)
    assert bb_source.block_of((0.26, 0.74, 0.7), a, SimpleNamespace(T=frames.SE2.identity())) == want


def test_candidates_carry_their_block_when_the_area_is_known():
    scene = load_scene("clean_bench")
    recs = records(scene)
    a = area((0.3, -0.2, 0.4), [[0.0] * 8] * 8, bounds=(-2.0, 2.0, -2.0, 2.0))
    for c in bb_source.candidates(mirror(scene.room, recs), REG, scene.room["zones"], area=a):
        assert c.block == bb_source.block_of(c.centroid, a, REG)


def keys_on_shelf(scene):
    return next(r for r in records(scene) if r.id == "keys_7c2e")


# dz picked so the shelf top's BB cells land just above 0.90625 m in the room, in the level-8
# grid cell [0.906, 0.9375) -- above the keys' 0.915 m top. Without dropping the surface the
# ray is blocked; the precondition assert keeps this test from passing for the wrong reason.
T_GRAZE = frames.SE2(theta=0.7, tx=1.3, ty=-0.4, dz=0.0001)
EYE = (0.62, 0.0, 0.95)            # 0.78 m in front of the keys, 3.5 cm above their top


def test_visible_grazing_ray_over_the_shelf_is_not_blocked_by_the_shelf():
    """Eye 3.5 cm above the keys' top, skimming the 0.90 m shelf. The surface an object stands
    on can't hide it (both ends of the ray are above it), so the grid must not let it."""
    scene = load_scene("clean_bench")
    pts, _ = bb_source.room_points(mirror(scene.room, records(scene), T=T_GRAZE), SimpleNamespace(T=T_GRAZE))
    origin, size, _ = voxelize.pinned_cube()
    raw = voxelize.VoxelGrid.from_points(pts, cube=(origin, size, 8), min_pts=1)
    assert not bb_source.visible(keys_on_shelf(scene), raw, EYE), "precondition: the raw shelf top blocks"
    assert bb_source.visible(keys_on_shelf(scene), bb_source.visibility_grid(pts, scene.room["zones"]), EYE)


def test_visible_an_occluder_hides_it():
    scene = load_scene("clean_bench")
    board = ObjectRecord("board_0001", "board", "shelf", Pose(0.62, 0.68, 0.90 + 0.15, 0), Extents(0.30, 0.04, 0.30),
                         "#ffffff", "2026-09-19T00:00:00Z")
    pts, _ = bb_source.room_points(mirror(scene.room, records(scene) + [board]), REG)
    grid = bb_source.visibility_grid(pts, scene.room["zones"])
    assert not bb_source.visible(keys_on_shelf(scene), grid, EYE)
    assert bb_source.visible(keys_on_shelf(scene), grid, (0.62, 1.40, 1.20))     # from behind the shelf: clear


# ── 4 · the costmap on BB's own 3 cm grid ─────────────────────────────────────────────

ANCHOR = (0.5, -0.2, 0.3)          # BB world pose of the robot when the area was defined


def area_to_room(X, Y):
    x, y = frames.robot_rel_to_bb(X, Y, *ANCHOR)
    return frames.bb_to_room((x, y, 0.0), T)[:2]


def bb_area_with_a_table():
    """3 x 3 m of floor in the area frame, a 0.3 m table leg footprint at area (0.6, 0.0)."""
    g = np.ones((100, 100), np.uint8)
    g[46:52, 66:72] = 2      # an 18 cm leg: rows = Y from -1.5, cols = X from -1.5. Small enough
                             # that a stance exists INSIDE costmap.REACH_MARGIN, which is the point
                             # of this test -- the margin itself is pinned in test_costmap.py
    return area(ANCHOR, [[0.0] * 6] * 6, bounds=(-1.5, 1.5, -1.5, 1.5), grid=g)


def test_costmap_from_bb_grid_puts_obstacles_where_bb_saw_them():
    cm = costmap.Costmap.from_bb_grid(bb_area_with_a_table(), REG)
    assert cm.occupied(*area_to_room(0.6, 0.0))               # the leg itself
    assert cm.occupied(*area_to_room(0.6 + 0.09 + 0.2, 0.0))  # within the robot's radius of its edge
    assert not cm.occupied(*area_to_room(-0.8, -0.8))         # open floor
    assert cm.occupied(*area_to_room(2.5, 0.0))               # outside the area: unknown, can't stand
    assert cm.obstacle.sum() > 0 and cm.grid.leaf == pytest.approx(8.0 / 256)


class Arm:
    r_min, r_max = 0.18, 0.48

    def reachable(self, target, base_pose):
        return self.r_min <= math.dist(target[:2], base_pose[:2]) <= self.r_max


def test_solvers_work_on_the_bb_costmap():
    cm = costmap.Costmap.from_bb_grid(bb_area_with_a_table(), REG)
    target = (*area_to_room(0.6, 0.0), 0.72)
    start = (*area_to_room(-0.8, -0.8), 0.0)
    pose, why = costmap.solve_base_pose_why(target, cm, Arm(), start, eye_h=0.95)
    assert pose is not None, why
    assert not cm.occupied(pose[0], pose[1])
    assert Arm.r_min <= math.dist(pose[:2], target[:2]) <= Arm.r_max * costmap.REACH_MARGIN
    view = costmap.solve_viewpoint(target, cm, blocked_from=start[:2], robot_pose=start, eye_h=0.95)
    assert view is not None and not cm.occupied(view[0], view[1])


# ── 5 · G2 across passes ──────────────────────────────────────────────────────────────

def test_g2_an_untouched_room_stays_clean_across_five_passes(tmp_path):
    """The acceptance: each pass re-observes the room with fresh noise (3% of object cells
    dropped, colour +-6, stray speckle) and scan_into_bb writes the tree; after the first
    commit, `git status` is empty after every pass."""
    scene = load_scene("clean_bench")
    recs = records(scene)
    repo = roomrepo.init(tmp_path / "room", scene.room)
    eye = frames.room_to_bb((-0.4, 0.0, 0.0), T)
    state = SimpleNamespace(x=eye[0], y=eye[1], h=frames.heading_room_to_bb_yaw(0.0, T), ready=True, map_gen=1)
    for n in range(5):
        nav = SimpleNamespace(mirror=mirror(scene.room, recs, drop=0.03, rgb_noise=6, strays=5, seed=n),
                              area=None, state=state)
        res = bb_source.scan_into_bb(repo.path, nav, REG)
        assert res.ok, res
        if n == 0:
            assert len(list((repo.path / "zones").rglob("*.yaml"))) == len(recs)
            subprocess.run(["git", "-C", str(repo.path), "add", "-A"], check=True)
            subprocess.run(["git", "-C", str(repo.path), "-c", "user.name=t", "-c", "user.email=t@t",
                            "commit", "-qm", "first scan"], check=True)
        else:
            dirty = subprocess.run(["git", "-C", str(repo.path), "status", "--porcelain", "--", "zones"],
                                   capture_output=True, text=True, check=True).stdout
            assert dirty == "", f"pass {n}: {dirty}"


def test_accuracy_holds_at_any_angle_between_bb_and_the_room():
    """T comes from a tag sighting, so BB's lattice can sit at ANY angle to the objects. Swept:
    centroid <= 1 cm and sides <= 1.5 cm everywhere; yaw +-5 deg for anything at least 4 cells
    (6 cm) across. The 8 x 4 cm keys are under 3 cells wide: their yaw is good to ~10 deg, the
    resolution's limit, not a bug (32-angle sweep: worst 10, mean 3)."""
    for name in ("clean_bench", "messy_bench"):
        scene = load_scene(name)
        recs = records(scene)
        for th in np.linspace(0.05, 1.5, 6):
            t = frames.SE2(float(th), 1.3, -0.4, 0.05)
            cands = bb_source.candidates(mirror(scene.room, recs, T=t), SimpleNamespace(T=t), scene.room["zones"])
            assert len(cands) == len(recs), (name, th)
            for rid, c in match(cands, recs).items():
                r = next(x for x in recs if x.id == rid)
                assert math.dist(c.centroid, (r.pose.x, r.pose.y, r.pose.z)) <= 0.012, (name, th, rid)
                if r.extents.x / r.extents.y >= 1.3:
                    tol = 5 if r.extents.y >= 0.06 else 12
                    assert axis_err(c.yaw_axis_deg, r.pose.yaw, 180) <= tol, (name, th, rid, c.yaw_axis_deg)
                    got, want = c.extents, (r.extents.x, r.extents.y, r.extents.z)
                else:
                    got, want = (*sorted(c.extents[:2]), c.extents[2]), (*sorted((r.extents.x, r.extents.y)), r.extents.z)
                assert max(abs(g - w) for g, w in zip(got, want)) <= 0.015, (name, th, rid, got, want)


def _pass(repo, scene, recs, state, area=None, seed=0):
    nav = SimpleNamespace(mirror=mirror(scene.room, recs, seed=seed), area=area, state=state)
    return bb_source.scan_into_bb(repo.path, nav, REG)


def _commit(repo):
    subprocess.run(["git", "-C", str(repo.path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo.path), "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-qm", "scan"], check=True)


def _file_near(repo, x, y) -> Path:
    """A first scan mints its own ids: find the committed file whose pose is at (x, y)."""
    from roomctl.state import read_tree
    rec = min(read_tree(repo.path).values(), key=lambda r: math.dist((r.pose.x, r.pose.y), (x, y)))
    assert math.dist((rec.pose.x, rec.pose.y), (x, y)) < 0.02
    return repo.path / "zones" / rec.zone / f"{rec.id}.yaml"


def _state_at(x, y):
    p = frames.room_to_bb((x, y, 0.0), T)
    return SimpleNamespace(x=p[0], y=p[1], h=0.0, ready=True, map_gen=1)


def test_scenario_3_an_occluded_object_is_never_reported_missing(tmp_path):
    """bbsim scenario 3. The keys' cells are gone from the map AND a board stands between the
    eye and their last pose: `unobserved`, the file kept, pass after pass. Take the board away
    with the keys still gone and the block fresh: now it's a real miss, and after
    MISSES_TO_REMOVE of them, `removed`."""
    import associate
    scene = load_scene("clean_bench")
    recs = records(scene)
    repo = roomrepo.init(tmp_path / "room", scene.room)
    eye = _state_at(0.62, 0.0)                                  # looking at the shelf across the desk
    assert _pass(repo, scene, recs, eye).ok
    _commit(repo)
    k = keys_on_shelf(scene)
    keys = _file_near(repo, k.pose.x, k.pose.y)
    before = keys.read_text()
    board = ObjectRecord("board_0001", "board", "shelf", Pose(0.62, 0.68, 0.90 + 0.15, 0), Extents(0.30, 0.04, 0.30),
                         "#ffffff", "2026-09-19T00:00:00Z")
    without_keys = [r for r in recs if r.id != "keys_7c2e"]
    for n in range(3):
        res = _pass(repo, scene, without_keys + [board], eye, seed=n)
        assert res.verdicts.get(associate.UNOBSERVED, 0) >= 1 and not res.verdicts.get(associate.REMOVED), res.verdicts
        assert keys.read_text() == before
    for n in range(associate.MISSES_TO_REMOVE):
        res = _pass(repo, scene, without_keys, eye, seed=10 + n)
    assert res.verdicts.get(associate.REMOVED) == 1 and not keys.exists()


def test_a_stale_block_is_never_a_miss(tmp_path):
    """The keys gone, in plain sight, but BB hasn't looked at their block lately: unobserved."""
    import associate
    scene = load_scene("clean_bench")
    recs = records(scene)
    repo = roomrepo.init(tmp_path / "room", scene.room)
    eye = _state_at(0.62, 0.0)
    assert _pass(repo, scene, recs, eye).ok
    _commit(repo)
    k = keys_on_shelf(scene)
    keys = _file_near(repo, k.pose.x, k.pose.y)
    stale = area((0.0, 0.0, 0.0), [[60.0] * 16] * 16, bounds=(-4.0, 4.0, -4.0, 4.0))
    for n in range(associate.MISSES_TO_REMOVE + 1):
        res = _pass(repo, scene, [r for r in recs if r.id != "keys_7c2e"], eye, area=stale, seed=n)
        assert not res.verdicts.get(associate.REMOVED) and not res.verdicts.get(associate.MISSED), res.verdicts
    assert keys.exists()


class VLM:
    model = "fake-vlm"

    def __init__(self):
        self.calls = 0

    def __call__(self, jpeg: bytes) -> dict:
        self.calls += 1
        return {"label": "thing", "description": "something on the desk"}


def test_a_frame_names_new_objects_and_words_only_what_was_added(tmp_path):
    """The robot's frame (camera_frame: BB pose through frames, mount through fuse) gives a new
    object its segmenter class -- `lamp_…`, not `unknown_…` -- and VLM words for what this pass
    ADDED. A quiet second pass costs no VLM call and leaves the tree clean."""
    import json

    import fuse
    import segment
    scene = load_scene("clean_bench")
    recs = records(scene)
    repo = roomrepo.init(tmp_path / "room", scene.room)
    x, y, _ = frames.room_to_bb((-0.6, 0.0, 0.0), T)
    pose_bb = (x, y, frames.heading_room_to_bb_yaw(0.0, T))          # facing the desk (+X)
    img = np.full((480, 640, 3), 128, np.uint8)
    fr = bb_source.camera_frame(img, fuse.Mount(pitch_down_deg=20.0, height_m=1.0), pose_bb, REG, (400.0, 320.0, 240.0))
    m = mirror(scene.room, recs)
    cands = bb_source.candidates(m, REG, scene.room["zones"])
    lamp = next(c for c in cands if c.color == "#d9b650")
    mask = segment.project_footprints([lamp], img.shape[:2], fr.K, fr.room_to_cam) == 0
    assert mask.sum() > 500, "precondition: the frame sees the lamp"
    vlm, eye = VLM(), _state_at(-0.6, 0.0)
    seg = lambda image: [segment.Mask(mask, "lamp", 0.9)]  # noqa: E731
    nav = SimpleNamespace(mirror=m, area=None, state=eye)
    res = bb_source.scan_into_bb(repo.path, nav, REG, frame=fr, segmenter=seg, describe=True, vlm=vlm)
    names = sorted(p.stem.rsplit("_", 1)[0] for p in (repo.path / "zones").rglob("*.yaml"))
    assert res.verdicts == {"added": len(recs)} and names.count("lamp") == 1 and names.count("unknown") == len(recs) - 1
    assert vlm.calls >= 1
    staged = json.loads((repo.path / ".git" / "gitspace" / "scan.json").read_text())["meta_by_id"]
    lamp_id = next(k for k in staged if k.startswith("lamp_"))
    assert staged[lamp_id]["raw_description"] == ["something on the desk"] and staged[lamp_id]["vlm_model"] == "fake-vlm"
    _commit(repo)
    before = vlm.calls
    res = bb_source.scan_into_bb(repo.path, SimpleNamespace(mirror=mirror(scene.room, recs, seed=1), area=None, state=eye),
                                 REG, frame=fr, segmenter=seg, describe=True, vlm=vlm)
    assert res.verdicts == {"unchanged": len(recs)} and vlm.calls == before
    assert subprocess.run(["git", "-C", str(repo.path), "status", "--porcelain", "--", "zones"],
                          capture_output=True, text=True).stdout == ""


def test_labels_need_a_frame():
    scene = load_scene("clean_bench")
    nav = SimpleNamespace(mirror=mirror(scene.room, []), area=None, state=None)
    with pytest.raises(ValueError, match="frame"):
        bb_source.scan_into_bb("/nonexistent", nav, REG, segmenter=lambda im: [])


# ── bbos's own map: 3 cm cells, the SLAM frame, a floor label per cell ─────────────────

class LabelledMap(Mirror):
    """A source shaped like bb_source.MapSnapshot: points() plus floor_mask() in the same order."""

    def __init__(self, res, cells, floor):
        super().__init__(res, cells)
        self.floor = floor

    def floor_mask(self):
        return np.array([c in self.floor for c in self.cells], bool)


def labelled(room, recs, T=frames.SE2.identity(), res=0.03, **kw) -> LabelledMap:
    """The scene at bbos's 3 cm, plus the room's floor at z = 0, labelled floor by the map."""
    m = mirror(room, recs, T=T, res=res, **kw)
    x, y = np.meshgrid(np.arange(-0.6, 1.2, res / 2), np.arange(-0.8, 1.2, res / 2))
    fl = frames.room_to_bb_array(np.column_stack([x.ravel(), y.ravel(), np.full(x.size, 0.01)]), T)
    floor = {tuple(c) for c in np.unique(np.floor(fl / res).astype(int), axis=0).tolist()}
    for c in floor:
        m.cells.setdefault(c, (90, 90, 90))
    return LabelledMap(res, m.cells, floor)


def test_a_3cm_map_labelled_by_the_map_itself():
    """bbos's mapping.voxels: 3 cm, floor labelled per cell. Every object found; the floor never
    becomes one. At 3 cm a side is good to about a cell, a centre to about half of one; a yaw is
    reported only for footprints whose sides differ by 1.5 cells or more (else round, yaw 0)."""
    scene = load_scene("messy_bench")
    recs = records(scene)
    src = labelled(scene.room, recs)
    reg = bb_source.identity_registration(src)
    cands = bb_source.candidates(src, reg, scene.room["zones"])
    assert len(cands) == len(recs)
    for rid, c in match(cands, recs).items():
        r = next(x for x in recs if x.id == rid)
        assert math.dist(c.centroid, (r.pose.x, r.pose.y, r.pose.z)) <= 0.02, (rid, c.centroid)
        if c.fit.aspect() > 1.0:                                     # reported elongated
            assert axis_err(c.yaw_axis_deg, r.pose.yaw, 180) <= 10, (rid, c.yaw_axis_deg)
            got, want = c.extents, (r.extents.x, r.extents.y, r.extents.z)
        else:                   # round (yaw 0): the schema measures it along the room's axes
            assert c.yaw_axis_deg == 0
            co, si = abs(math.cos(math.radians(r.pose.yaw))), abs(math.sin(math.radians(r.pose.yaw)))
            aabb = (r.extents.x * co + r.extents.y * si, r.extents.x * si + r.extents.y * co)
            got, want = (*sorted(c.extents[:2]), c.extents[2]), (*sorted(aabb), r.extents.z)
        # a side is good to about a cell; one under two cells across (4 cm keys) to a cell and a half
        tol = 0.03 if min(r.extents.x, r.extents.y) >= 0.06 else 0.05
        assert max(abs(g - w) for g, w in zip(got, want)) <= tol, (rid, got, want)
    bare = LabelledMap(src.res, {c: v for c, v in src.cells.items() if c not in src.floor}, set())
    assert [c.centroid for c in bb_source.candidates(bare, reg, scene.room["zones"])] == [c.centroid for c in cands]


def test_g2_on_a_3cm_labelled_map_across_five_passes(tmp_path):
    """The source goes straight in (no BBNav): five passes, fresh noise each, clean after each."""
    scene = load_scene("clean_bench")
    recs = records(scene)
    repo = roomrepo.init(tmp_path / "room", scene.room)
    for n in range(5):
        src = labelled(scene.room, recs, drop=0.03, rgb_noise=6, strays=3, seed=n)
        res = bb_source.scan_into_bb(repo.path, src, bb_source.identity_registration(src))
        if n == 0:
            assert res.verdicts == {"added": len(recs)}
            _commit(repo)
        else:
            dirty = subprocess.run(["git", "-C", str(repo.path), "status", "--porcelain", "--", "zones"],
                                   capture_output=True, text=True).stdout
            assert dirty == "", f"pass {n}: {res.verdicts} {dirty}"


def _npz(path, origin=(-21.12, -21.12), pgo=222, floor_z=0.015):
    rng = np.random.default_rng(0)
    coords = np.vstack([np.column_stack([rng.uniform(-1, 1, 200), rng.uniform(-1, 1, 200), np.full(200, floor_z)]),
                        np.column_stack([rng.uniform(-1, 1, 50), rng.uniform(-1, 1, 50), rng.uniform(0.3, 1.2, 50)])])
    labels = np.r_[np.full(200, -1), np.full(50, 1)].astype(np.int8)
    np.savez_compressed(path, coords=coords.astype(np.float32), colors=np.full((250, 3), 128, np.uint8), labels=labels,
                        info=np.zeros((250, 4), np.int32), origin=np.array(origin), robot_pos=np.array([-1.04, 0.38]),
                        robot_heading=np.float32(0.63), timestamp_ns=np.int64(1789839118 * 10**9), pgo_count=np.int32(pgo))
    return path


def test_map_snapshot_reads_scripts_bbos_map_npz(tmp_path):
    snap = bb_source.MapSnapshot.load(_npz(tmp_path / "map.npz"))
    pts, rgb = snap.points()
    assert pts.shape == (250, 3) and rgb.dtype == np.uint8 and snap.res == 0.03
    assert snap.floor_mask().sum() == 200 and snap.robot_xy == pytest.approx((-1.04, 0.38)) and snap.at.endswith("Z")
    reg = bb_source.identity_registration(snap)
    assert reg.T == frames.SE2.identity() and reg.map_gen == snap.map_gen and reg.source == "identity"
    later = bb_source.MapSnapshot.load(_npz(tmp_path / "later.npz", pgo=227))           # loop closures: same map
    rebooted = bb_source.MapSnapshot.load(_npz(tmp_path / "reboot.npz", origin=(-19.5, -20.1)))
    assert later.map_gen == snap.map_gen and rebooted.map_gen != snap.map_gen


def test_identity_is_refused_when_the_maps_floor_is_not_at_zero(tmp_path):
    snap = bb_source.MapSnapshot.load(_npz(tmp_path / "map.npz", floor_z=0.5))
    with pytest.raises(ValueError, match="not the room frame"):
        bb_source.identity_registration(snap)


def test_a_map_from_another_slam_session_is_refused(tmp_path):
    snap = bb_source.MapSnapshot.load(_npz(tmp_path / "map.npz"))
    reg = bb_source.identity_registration(bb_source.MapSnapshot.load(_npz(tmp_path / "old.npz", origin=(0.0, 0.0))))
    with pytest.raises(ValueError, match="re-register"):
        bb_source.scan_into_bb(tmp_path, snap, reg)


def test_yaw_is_held_not_reported_where_a_3cm_map_cannot_see_it(tmp_path):
    """Master's real-table run: a 13 x 3 x 18 cm thing reported "turned 0 -> 140 deg". On a 3 cm
    map it is one column wide, and a 10 x 10 cm one is round: neither has a yaw to report. With
    a few mm of re-observation noise per pass, their raw estimates swing (the precondition), yet
    git never sees a turn -- the committed yaw is held, through real moves too."""
    scene = load_scene("clean_bench")
    thin = ObjectRecord("thin_0001", "thing", "desk", Pose(0.40, -0.10, 0.70 + 0.09, 30), Extents(0.13, 0.03, 0.18),
                        "#c0392b", "2026-09-19T00:00:00Z")
    square = ObjectRecord("square_0001", "thing", "desk", Pose(0.70, 0.20, 0.70 + 0.05, 20), Extents(0.10, 0.10, 0.10),
                          "#2e7d32", "2026-09-19T00:00:00Z")
    repo = roomrepo.init(tmp_path / "room", scene.room)
    rng = np.random.default_rng(7)
    raw = {"#c0392b": set(), "#2e7d32": set()}

    def jittered(recs):
        return [ObjectRecord(r.id, r.cls, r.zone, Pose(r.pose.x + rng.uniform(-0.008, 0.008), r.pose.y + rng.uniform(-0.008, 0.008),
                                                       r.pose.z, r.pose.yaw + rng.uniform(-4, 4)), r.extents, r.color, r.first_seen)
                for r in recs]

    for n in range(8):
        src = labelled(scene.room, jittered([thin, square]), seed=n)
        reg = bb_source.identity_registration(src)
        for c in bb_source.candidates(src, reg, scene.room["zones"]):
            raw[c.color].add(c.yaw_axis_deg)
            assert not c.fit.yaw_known()
        res = bb_source.scan_into_bb(repo.path, src, reg)
        if n == 0:
            _commit(repo)
        else:
            dirty = subprocess.run(["git", "-C", str(repo.path), "status", "--porcelain", "--", "zones"],
                                   capture_output=True, text=True).stdout
            assert res.verdicts == {"unchanged": 2} and dirty == "", (n, res.verdicts, dirty)
    assert max(max(v) - min(v) for v in raw.values()) > 10, f"precondition: the raw yaw never swung past the deadband {raw}"
    # settle only writes a yaw when the thing MOVED, so that is where a false turn shows: move it
    # for real, again and again, and the committed yaw must ride along unchanged
    from roomctl.state import read_tree
    first = {r.color: r.pose.yaw for r in read_tree(repo.path).values()}
    steps = [(0.12, 0.0), (0.0, 0.10), (-0.10, 0.0), (0.0, -0.12), (0.08, 0.08), (-0.08, -0.08)] * 2
    for n, (dx, dy) in enumerate(steps):
        thin = ObjectRecord(thin.id, thin.cls, thin.zone, Pose(thin.pose.x + dx, thin.pose.y + dy, thin.pose.z, thin.pose.yaw),
                            thin.extents, thin.color, thin.first_seen)
        src = labelled(scene.room, jittered([thin, square]), seed=100 + n)
        res = bb_source.scan_into_bb(repo.path, src, bb_source.identity_registration(src))
        assert res.verdicts == {"unchanged": 1, "moved": 1}, (n, res.verdicts)
        now = {r.color: r.pose.yaw for r in read_tree(repo.path).values()}
        assert now == first, f"move {n}: a turn nobody made {first} -> {now}"
        _commit(repo)


# ── what a real pass writes to the shared indices ─────────────────────────────────────

def _props(index):
    m = json.loads((REPO / "elastic" / "mappings" / f"{index}.json").read_text())
    return set(m.get("template", m)["mappings"]["properties"])


class FakeES:
    def __init__(self):
        self.ops = []

    def bulk(self, operations, refresh=None):
        pairs = list(zip(operations[0::2], operations[1::2]))
        self.ops += pairs
        return {"errors": False, "items": [{next(iter(a)): {"status": 201}} for a, _ in pairs]}

    def docs(self, index):
        return [(a, d) for a, d in self.ops if next(iter(a.values()))["_index"] == index]


def snapshot(room, recs, **kw) -> "bb_source.MapSnapshot":
    """The same 3 cm scene as bbos's own arrays: what a pull from the robot looks like."""
    src = labelled(room, recs, **kw)
    cells = list(src.cells)
    return bb_source.MapSnapshot((np.array(cells, float) + 0.5) * src.res,
                                 np.array([src.cells[c] for c in cells], np.uint8),
                                 np.array([-1 if c in src.floor else 1 for c in cells], np.int8),
                                 np.array([-21.12, -21.12]), 222, (-1.0, 0.4), "2026-09-19T18:00:00Z")


@pytest.fixture
def clouds(tmp_path, monkeypatch):
    """The .ply beside the catalog doc goes to a scratch clouds/, never the repo's."""
    import pipeline
    monkeypatch.setattr(pipeline, "CLOUDS", tmp_path / "clouds")
    return tmp_path / "clouds"


def test_a_real_map_pass_writes_bb_map_observations(tmp_path, clouds):
    """plan/roommate 03 section 10: one room-observations row per object, camera "bb_map", and the
    room-clouds doc /capture/<id> looks the capture up by -- both through es_sink, both carrying
    this pass's Sentry trace. No gate fields: a map pass has no shutter."""
    scene = load_scene("clean_bench")
    recs = records(scene)
    repo = roomrepo.init(tmp_path / "room", scene.room)
    snap = snapshot(scene.room, recs)
    es = FakeES()
    res = bb_source.scan_into_bb(repo.path, snap, bb_source.identity_registration(snap), es=es)
    rows = es.docs("room-observations")
    [(cloud_action, cloud)] = es.docs("room-clouds")
    assert len(rows) == res.objects == len(recs)
    for action, d in rows:
        assert action == {"create": {"_index": "room-observations"}}          # a TSDS: create only
        assert set(d) <= _props("room-observations"), set(d) - _props("room-observations")
        assert d["camera"] == "bb_map" and d["capture_id"] == res.capture_id
        assert d["sentry_trace_id"] == cloud["sentry_trace_id"]                # one waterfall
    assert sorted(d["object_id"] for _, d in rows) == sorted(p.stem for p in (repo.path / "zones").rglob("*.yaml"))
    assert cloud_action == {"index": {"_index": "room-clouds", "_id": res.capture_id}}
    assert set(cloud) <= _props("room-clouds") and cloud["cameras"] == ["bb_map"]
    ply = Path(cloud["cloud_uri"].removeprefix("file://"))          # the blob beside the catalog doc
    assert ply.parent == clouds and f"element vertex {cloud['point_count']}\n".encode() in ply.read_bytes()[:200]
    assert cloud["point_count"] == len(snap.points()[0]) - int(snap.floor_mask().sum())
    assert not {"skew_ms", "tilt_rate_max", "coverage_pct", "quality_ok"} & set(cloud)
    assert cloud["objects"] == len(recs)          # capture_docs' count, same three states


def test_a_simulated_pass_is_never_indexed(tmp_path, caplog):
    """MVP-NOW's rule: simulated data never reaches the shared indices as if it were real. A
    bbsim nav is loopback by construction, and a source with no provenance isn't written either
    -- an allow-list, so a new kind of fake can't quietly qualify."""
    scene = load_scene("clean_bench")
    recs = records(scene)
    repo = roomrepo.init(tmp_path / "room", scene.room)
    src = labelled(scene.room, recs)
    reg = bb_source.identity_registration(src)
    for nav, why in ((src, "carries no provenance"),
                     (SimpleNamespace(mirror=src, host="127.0.0.1", state=None, area=None), "bbsim binds loopback"),
                     (SimpleNamespace(mirror=src, synthetic=True, state=None, area=None), "says it is synthetic")):
        es = FakeES()
        with caplog.at_level("WARNING"):
            caplog.clear()
            bb_source.scan_into_bb(repo.path, nav, reg, es=es)
        assert es.ops == [], why
        assert why in caplog.text, caplog.text
    assert bb_source.live_source(SimpleNamespace(mirror=src, host="192.168.2.10"))[0]       # the real robot
    assert bb_source.live_source(snapshot(scene.room, recs))[0]                             # a real pull
    # a sim signal wins over real-looking provenance: a saved map replayed through a loopback
    # harness is a sim run, and the shared indices are not where a rehearsal goes
    replay = SimpleNamespace(mirror=snapshot(scene.room, recs), host="127.0.0.1")
    assert bb_source.live_source(replay) == (False, "a nav on 127.0.0.1: bbsim binds loopback")


def test_without_es_a_pass_writes_no_documents(tmp_path):
    scene = load_scene("clean_bench")
    repo = roomrepo.init(tmp_path / "room", scene.room)
    snap = snapshot(scene.room, records(scene))
    es = FakeES()
    bb_source.scan_into_bb(repo.path, snap, bb_source.identity_registration(snap))
    assert es.ops == []


def test_the_env_default_only_publishes_from_the_rooms_own_repo(tmp_path, monkeypatch, clouds):
    """GITSPACE_INDEX_CAPTURES=1 turns indexing on, but a scratch or sim repo still writes
    nothing: roomctl/cli.py's rule (publish.is_the_room, keyed on $ROOM_GIT_PATH), so there is
    one rule for both paths and no live call from a test."""
    scene = load_scene("clean_bench")
    repo = roomrepo.init(tmp_path / "room", scene.room)
    snap = snapshot(scene.room, records(scene))
    sent = []
    monkeypatch.setattr(bb_source, "index_map_scan", lambda docs, capture_id, es: sent.append(es))
    monkeypatch.setenv("GITSPACE_INDEX_CAPTURES", "1")
    monkeypatch.setenv("ROOM_GIT_PATH", str(tmp_path / "elsewhere"))
    bb_source.scan_into_bb(repo.path, snap, bb_source.identity_registration(snap))
    assert sent == []
    monkeypatch.setenv("ROOM_GIT_PATH", str(repo.path))
    bb_source.scan_into_bb(repo.path, snap, bb_source.identity_registration(snap))
    assert sent == ["env"]


# ── the cells an object leaves behind ─────────────────────────────────────────────────

def test_a_candidate_on_a_just_vacated_pose_is_never_acted_on(tmp_path):
    """Measured on the sim: after a move, the map keeps the object's cells at its OLD pose for
    seconds to half a minute, so there is one blob more than there are objects. Nothing may be
    minted for it -- no `added` phantom to become a lost_and_found chore, and no neighbour
    "moved" onto the empty spot to become a tidy. The lamp moving must be the whole story."""
    scene = load_scene("clean_bench")
    recs = records(scene)
    repo = roomrepo.init(tmp_path / "room", scene.room)
    src = snapshot(scene.room, recs)
    bb_source.scan_into_bb(repo.path, src, bb_source.identity_registration(src))
    _commit(repo)
    before = sorted(p.name for p in (repo.path / "zones").rglob("*.yaml"))
    lamp = next(r for r in recs if r.id == "lamp_2d9b")
    moved = ObjectRecord(lamp.id, lamp.cls, lamp.zone, Pose(0.62, 0.35, lamp.pose.z, lamp.pose.yaw),
                         lamp.extents, lamp.color, lamp.first_seen)
    stale = ObjectRecord("stale_0001", lamp.cls, lamp.zone, Pose(lamp.pose.x, lamp.pose.y, 0.70 + 0.03, 0),
                         Extents(0.05, 0.05, 0.06), lamp.color, lamp.first_seen)   # what the map kept
    src2 = snapshot(scene.room, [r for r in recs if r.id != lamp.id] + [moved, stale])
    assert any(c.centroid[0] == pytest.approx(lamp.pose.x, abs=0.03) and c.centroid[1] == pytest.approx(lamp.pose.y, abs=0.03)
               for c in bb_source.candidates(src2, bb_source.identity_registration(src2), scene.room["zones"])), \
        "precondition: the stale cells are a candidate of their own"
    lamp_file = _file_near(repo, lamp.pose.x, lamp.pose.y)      # a first scan mints its own ids
    res = bb_source.scan_into_bb(repo.path, src2, bb_source.identity_registration(src2))
    assert not res.verdicts.get("added"), res.verdicts          # no phantom object was invented
    assert res.verdicts.get("moved") == 1, res.verdicts         # the lamp moving is the whole story
    assert sorted(p.name for p in (repo.path / "zones").rglob("*.yaml")) == before
    dirty = subprocess.run(["git", "-C", str(repo.path), "status", "--porcelain", "--", "zones"],
                           capture_output=True, text=True).stdout.split()
    assert dirty == ["M", str(lamp_file.relative_to(repo.path))], dirty   # one file, the one that moved


def test_hold_ghosts_never_lets_a_phantom_carry_a_neighbours_name():
    """The clicked-run failure: a `mess mug_a1b2` confirmed glasses_case_d04f:tidy-1, the glasses
    case being 16 cm from the pose the mug had just left. The neighbour is carried as unobserved
    -- its file untouched -- instead of being reported as having moved onto the empty spot."""
    import associate
    from roomctl.state import MOVE_M
    mug = ObjectRecord("mug_a1b2", "mug", "desk", Pose(0.42, 0.18, 0.75, 15), Extents(0.12, 0.09, 0.11), "#2b4c7e", "t")
    glasses = ObjectRecord("glasses_case_d04f", "glasses case", "desk", Pose(0.36, 0.02, 0.72, 60),
                           Extents(0.15, 0.06, 0.04), "#34495e", "t")
    box = lambda c: (np.array(c), np.array([0.1, 0.1, 0.1]), 0.0)  # noqa: E731
    obj = object()
    assocs = [
        associate.Association("moved", mug.id, mug.cls, mug.color, "t", "desk", obj, mug, *box((0.25, 0.34, 0.75))),
        associate.Association("moved", glasses.id, glasses.cls, glasses.color, "t", "desk", obj, glasses, *box((0.43, 0.17, 0.72))),
    ]
    out, held = bb_source.hold_ghosts(assocs, MOVE_M)
    assert held == [("glasses_case_d04f", "mug_a1b2")]
    assert [(a.object_id, a.verdict, a.obj is None) for a in out] == [
        ("mug_a1b2", "moved", False), ("glasses_case_d04f", associate.UNOBSERVED, True)]
    assert "mug_a1b2" in out[1].note
    assert bb_source.hold_ghosts(assocs[:1], MOVE_M) == (assocs[:1], [])      # nothing left, nothing held
