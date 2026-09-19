"""Refresh localhost telemetry snapshots from the robot without moving it."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'web')]
import bbos_map
import camera_ingest
import numpy as np


def publish_map(folder):
    out = ROOT / 'web/landing/live'
    out.mkdir(parents=True, exist_ok=True)
    metadata = json.loads((folder / 'objects.json').read_text())
    with np.load(folder / 'map.npz') as source:
        coords, colors = source['coords'], source['colors']
        # Uniform sample across the whole measured map, retaining original sensor coordinates.
        indices = np.linspace(0, len(coords) - 1, min(len(coords), 20000), dtype=int)
        metadata.update(sender_mode='hardware', total=len(coords), cells=[
            {'center': [round(float(v), 4) for v in coords[i]], 'size': .03,
             'color': '#' + ''.join(f'{int(v):02x}' for v in colors[i])} for i in indices])
    pending = out / 'robot-map.tmp'
    pending.write_text(json.dumps(metadata))
    pending.replace(out / 'robot-map.json')
    print(f"Published hardware map: {metadata['total']} voxels at {metadata['at']}")


if __name__ == '__main__':
    if len(sys.argv) > 1:
        publish_map(Path(sys.argv[1]))
    else:
        host = bbos_map.target().split('@')[-1]
        sender = f'http://{host}:8080'
        capture = camera_ingest.pull(sender)
        if capture.get('sender_mode') != 'hardware':
            raise SystemExit('Refusing non-hardware capture')
        out = ROOT / 'web/landing/live'
        camera_ingest.publish(camera_ingest.from_robot(capture, out, sender), out)
        publish_map(bbos_map.pull())
