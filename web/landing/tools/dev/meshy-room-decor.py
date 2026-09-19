"""One decorative camera prop for the Room page. Submit once, then poll the saved task."""
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'models' / 'room-decor'
LOG = OUT / 'generation.json'
API = 'https://api.meshy.ai/openapi/v2/text-to-3d'
key = os.environ.get('MESHY_API_KEY')
if not key:
    config = Path.home() / 'Dev/projects/2026/test-run-3d/.env'
    if config.exists():
        for line in config.read_text().splitlines():
            if line.strip().startswith('MESHY_API_KEY='):
                key = line.strip().split('=', 1)[1].strip().strip('\"\'')
if not key:
    raise SystemExit('Meshy key unavailable')

def call(method, url, body=None):
    request = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
        headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)

OUT.mkdir(parents=True, exist_ok=True)
record = json.loads(LOG.read_text()) if LOG.exists() else None
if sys.argv[1] == 'submit' and record is None:
    prompt = ('A single industrial surveillance camera head in a chunky U shaped gimbal bracket, '
        'one very large recessed circular glass lens in a boxy beveled housing, concentric lens rings, '
        'deep vent slots, sturdy pivot bolts on both sides, compact ribbed rear casing, '
        'a short mounting socket beneath the bracket. Mechanical manga science fiction prop, '
        'worn pale metal with deep black recesses, bold clean readable silhouette, restrained hard surface detail. '
        'Camera points forward, front three quarter presentation. Isolated single object, '
        'no pedestal, no floor, no scene, no writing, no logo, no human, no cables, no long arm.')
    result = call('POST', API, {'mode': 'preview', 'prompt': prompt, 'ai_model': 'meshy-6',
        'should_remesh': True, 'topology': 'triangle', 'target_polycount': 6000, 'target_formats': ['glb']})
    record = {'task_id': result['result'], 'prompt': prompt, 'status': 'SUBMITTED'}
    LOG.write_text(json.dumps(record, indent=2))
if sys.argv[1] == 'refine' and record and not record.get('refine_task_id'):
    result = call('POST', API, {'mode': 'refine', 'preview_task_id': record['task_id'],
        'ai_model': 'meshy-6', 'enable_pbr': True, 'texture_resolution': '2k', 'target_formats': ['glb'],
        'texture_prompt': 'Weathered ivory painted industrial metal camera, chipped edges exposing dark graphite steel, dark rubber seals, charcoal black lens barrel, deep smoky blue glass lens with subtle blue reflections, worn brass pivot bolts, restrained ochre safety markings on small side panels, grease in recessed vents, fine surface scratches. Realistic tactile used machinery. No text or logos. Keep the main housing pale off-white, mostly monochrome with small muted colored accents.'})
    record['refine_task_id'] = result['result']
    record['refine_status'] = 'SUBMITTED'
    LOG.write_text(json.dumps(record, indent=2))
if sys.argv[1] == 'poll-refine' and record and record.get('refine_task_id') and record.get('refine_status') != 'SUCCEEDED':
    task = call('GET', API + '/' + record['refine_task_id'])
    record.update(refine_status=task['status'], refine_progress=task.get('progress'))
    if task['status'] == 'SUCCEEDED':
        urllib.request.urlretrieve(task['model_urls']['glb'], OUT / 'camera-textured.glb')
        if task.get('thumbnail_url'):
            urllib.request.urlretrieve(task['thumbnail_url'], OUT / 'preview-textured.png')
        record['refine_credits'] = task.get('consumed_credits')
        record['textured_bytes'] = (OUT / 'camera-textured.glb').stat().st_size
    LOG.write_text(json.dumps(record, indent=2))
if sys.argv[1] == 'poll' and record and record['status'] != 'SUCCEEDED':
    task = call('GET', API + '/' + record['task_id'])
    record.update(status=task['status'], progress=task.get('progress'))
    if task['status'] == 'SUCCEEDED':
        urllib.request.urlretrieve(task['model_urls']['glb'], OUT / 'camera.glb')
        if task.get('thumbnail_url'):
            urllib.request.urlretrieve(task['thumbnail_url'], OUT / 'preview.png')
        record['consumed_credits'] = task.get('consumed_credits')
        record['bytes'] = (OUT / 'camera.glb').stat().st_size
    if task['status'] == 'FAILED':
        record['error'] = task.get('task_error', {}).get('message', 'Generation failed')
    LOG.write_text(json.dumps(record, indent=2))
print(json.dumps({k: record.get(k) for k in ['status', 'progress', 'consumed_credits', 'bytes', 'refine_status', 'refine_progress', 'refine_credits', 'error']}))
