"""Two user-requested untextured Meshy props. Submit once; poll without resubmitting."""
import json
import os
import sys
import urllib.request
from pathlib import Path
from dotenv import dotenv_values

OUT = Path(__file__).resolve().parents[1] / 'models'
LOG = OUT / 'decor.json'
API = 'https://api.meshy.ai/openapi/v2/text-to-3d'
PROMPTS = {
    'relay': 'A single compact industrial telemetry relay cube, beveled square chassis, recessed square grid grille on each face, four thick rounded corner bumpers and a few inset screws. Clean chunky mechanical geometry, retrofuturist robotics equipment, off-white painted metal and dark recesses, monochrome manga science fiction prop. Restrained detail, readable silhouette at small size. No writing, no logos, no floor, no stand, no scene, no wires, no antennas. Isolated centered object.',
    'sensor': 'A single small industrial optical sensor pod in a thick U-shaped gimbal bracket. One recessed circular lens, concentric protective rings, ribbed rear housing, chunky short side pivots, compact rounded rectangular shell. Off-white painted metal with deep black mechanical recesses, monochrome manga robotics equipment. Clean readable geometry, restrained hard surface detail. No writing, no logos, no floor, no pedestal, no scene, no cables. Isolated centered object.',
}

key = os.environ.get('MESHY_API_KEY') or dotenv_values(Path.home() / 'Dev/projects/2026/test-run-3d/.env').get('MESHY_API_KEY')
if not key:
    raise SystemExit('Meshy key not available')


def call(method, url, body=None):
    request = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body else None,
        headers={'Authorization':f'Bearer {key}', 'Content-Type':'application/json'})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


OUT.mkdir(exist_ok=True)
log = json.loads(LOG.read_text()) if LOG.exists() else {}
for name, prompt in PROMPTS.items():
    if sys.argv[1] == 'submit' and name not in log:
        task = call('POST', API, {'mode':'preview', 'prompt':prompt, 'ai_model':'latest',
            'should_remesh':True, 'topology':'triangle', 'target_polycount':3000, 'target_formats':['glb']})
        log[name] = {'task_id':task['result'], 'prompt':prompt, 'status':'SUBMITTED'}
        LOG.write_text(json.dumps(log, indent=2))
    if sys.argv[1] == 'poll' and name in log and log[name]['status'] != 'SUCCEEDED':
        task = call('GET', API + '/' + log[name]['task_id'])
        log[name]['status'] = task['status']
        log[name]['progress'] = task.get('progress')
        if task['status'] == 'SUCCEEDED':
            urllib.request.urlretrieve(task['model_urls']['glb'], OUT / f'decor_{name}.glb')
            if task.get('thumbnail_url'):
                urllib.request.urlretrieve(task['thumbnail_url'], OUT / f'decor_{name}.png')
            log[name]['consumed_credits'] = task.get('consumed_credits')
            log[name]['bytes'] = (OUT / f'decor_{name}.glb').stat().st_size
        LOG.write_text(json.dumps(log, indent=2))
    if name in log:
        print(name, log[name]['status'], log[name].get('progress'), log[name].get('consumed_credits'))
