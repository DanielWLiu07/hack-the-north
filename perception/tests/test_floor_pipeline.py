"""Floor packets must survive the pipeline, including when the robot has moved."""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import difference
import pipeline
import segment

DATA = Path.home() / '.cache/gitspace/datasets'


@pytest.fixture(scope='module')
def packet():
    path = DATA / 'now/cap_0015'
    if not (path / 'capture.json').exists():
        pytest.skip('Hardware chip-packet recording is not installed')
    return difference.load_view(path)


def test_floor_default_and_explicit_opt_out(monkeypatch):
    monkeypatch.delenv('GITSPACE_FLOOR', raising=False)
    assert pipeline._floor()
    monkeypatch.setenv('GITSPACE_FLOOR', 'off')
    assert not pipeline._floor()


def test_packet_mask_does_not_depend_on_room_origin(packet):
    cam, view = packet
    def run(pose):
        return segment.run(view.xyz, view.valid, view.image, cam, segmenter=lambda _: [],
                           mount=view.mount, robot_pose=pose, floor=True)[0]
    base, moved = run((0., 0., 0.)), run((8., -5., 1.1))
    assert len(base) == len(moved) == 2
    pairs = list(zip(sorted(base, key=lambda i: i.mask.sum()), sorted(moved, key=lambda i: i.mask.sum())))
    for a, b in pairs:
        np.testing.assert_array_equal(a.mask, b.mask)
    near, moved_near = min(pairs, key=lambda ab: abs(ab[0].centroid[1] + 0.90))
    assert near.centroid[:2] == pytest.approx((.45, -.90), abs=.08)
    c, s = np.cos(1.1), np.sin(1.1)
    expected = near.points @ np.array([[c,s,0],[-s,c,0],[0,0,1]]) + [8,-5,0]
    np.testing.assert_allclose(moved_near.points, expected, atol=1e-6)


def test_packet_survives_without_image_model(packet, monkeypatch):
    cam, view = packet
    monkeypatch.delenv('GITSPACE_FLOOR', raising=False)
    zones = {'floor': {'min': [-4,-4,-.05], 'max': [4,4,.35], 'surface': 0.}}
    instances = pipeline.segment_then_cluster({cam: (view.xyz, view.valid, view.image)},
        {cam: view.mount}, (0.,0.,0.), zones, None, frozenset({'person'}))
    packets = [i for i in instances if i.source == 'floor']
    assert len(instances) == len(packets) == 2
    near = min(packets, key=lambda i: abs(i.centroid[1] + 0.90))
    far = min(packets, key=lambda i: abs(i.centroid[0] - 1.33))
    assert near.centroid[:2] == pytest.approx((.45,-.90), abs=.08)
    assert far.centroid[:2] == pytest.approx((1.33, .14), abs=.10)
    assert len(near.points) > 250
    assert len(far.points) > 60


def test_chip_bag_masks_are_the_bag_not_the_grey_halo(packet):
    """The matcher's halo around a bag is grey floor. PACK_SEED_FRAC trims it off.

    This asked the MEDIAN mask pixel to be saturated, which held only while the mask was the packet's
    printed end alone. A crisp packet is part printed and part pale foil, and floor_objects' BRIGHT_MARGIN
    now keeps that foil (measured: the mask used to hold 24-42 % of its own bounding box against 76 % for
    the can, and on cap_0018 it was a ring around a hole). The foil is genuinely unsaturated -- median
    chroma 6, median grey 200-210 against a floor p90 of 155-165 -- so a median-saturation bar now rejects
    a mask that is MORE of the real packet, not less. What still separates bag from halo is that the mask
    stays anchored on a large coloured core: measured 43-54 % of these masks, against ~0 for grey floor."""
    import cv2
    cam, view = packet
    inst = segment.run(view.xyz, view.valid, view.image, cam, segmenter=lambda _: [],
                       mount=view.mount, robot_pose=view.pose, floor=True)[0]
    sat = cv2.cvtColor(view.image, cv2.COLOR_BGR2HSV)[:, :, 1]
    assert len(inst) == 2
    for i in inst:
        assert float((sat[i.mask] >= 40).mean()) >= 0.30


def test_automatic_floor_path_leaves_desk_only_rooms_alone(monkeypatch):
    monkeypatch.delenv('GITSPACE_FLOOR', raising=False)
    assert not pipeline._floor({'desk': {'min': [0,0,.68], 'max': [1,1,1.3]}})
    assert pipeline._floor({'floor': {'min': [-2,-2,-.05], 'max': [2,2,.35]}})


@pytest.mark.parametrize('capture', ['cap_0007', 'cap_0008', 'cap_0010', 'cap_0011'])
def test_empty_floor_does_not_return_disparity_streaks(capture, monkeypatch):
    path = DATA / 'hallway-untouched' / capture
    if not (path / 'capture.json').exists():
        pytest.skip('Hardware hallway recording is not installed')
    monkeypatch.delenv('GITSPACE_FLOOR', raising=False)
    cam, v = difference.load_view(path)
    result = pipeline.segment_then_cluster({cam:(v.xyz,v.valid,v.image)}, {cam:v.mount},
        v.pose, {'floor':{'min':[-4,-4,-.05],'max':[4,4,.35]}}, None, frozenset({'person'}))
    assert result == []


def test_chair_foot_slivers_are_not_chip_bags():
    """cap_0014: saturation seed would take 1–2 cm slivers at the chair foot. MIN_WIDTH_M."""
    path = DATA / 'now' / 'cap_0014'
    if not (path / 'capture.json').exists():
        pytest.skip('Hardware chair recording is not installed')
    cam, v = difference.load_view(path)
    inst = segment.run(v.xyz, v.valid, v.image, cam, segmenter=lambda _: [],
                       mount=v.mount, robot_pose=v.pose, floor=True)[0]
    assert [i.source for i in inst] == []


def test_hardware_packet_reaches_room_files(packet, tmp_path, monkeypatch):
    """The recording ships the desk-only room.yaml and `zones/floor/**` in .roomignore.
    A hallway capture has none of its cloud on the desk, so the floor path must still
    run, invent a floor zone for the scan, and write both chip bags."""
    import yaml
    capture = DATA / 'now/cap_0015'
    monkeypatch.setenv('GITSPACE_SEGMENTER', 'off')
    monkeypatch.delenv('GITSPACE_FLOOR', raising=False)
    monkeypatch.delenv('GITSPACE_INDEX_CAPTURES', raising=False)
    result = pipeline.scan_into(tmp_path / 'room', capture, describe=False)
    assert result.ok and result.objects == 2
    files = list((tmp_path / 'room' / 'zones/floor').glob('*.yaml'))
    assert len(files) == 2
    poses = [yaml.safe_load(f.read_text())['pose'] for f in files]
    xy = sorted((p['x'], p['y']) for p in poses)
    assert xy[0][0] == pytest.approx(.44, abs=.08) and xy[0][1] == pytest.approx(-.90, abs=.08)
    assert xy[1][0] == pytest.approx(1.33, abs=.10) and xy[1][1] == pytest.approx(.14, abs=.10)
    room = yaml.safe_load((tmp_path / 'room' / 'room.yaml').read_text())
    assert 'floor' not in room.get('zones', {})


def test_hallway_cloud_turns_the_floor_path_on_without_a_floor_zone(packet, monkeypatch):
    monkeypatch.delenv('GITSPACE_FLOOR', raising=False)
    desk = {'desk': {'min': [0.08, -0.50, 0.68], 'max': [1.00, 0.50, 1.30]}}
    assert not pipeline._floor(desk)
    cam, view = packet
    from fuse import rect_to_world
    cloud = rect_to_world(view.xyz[view.valid], view.mount)
    assert pipeline._floor(desk, cloud)
    assert 'floor' in pipeline.ensure_floor_zone(desk)
