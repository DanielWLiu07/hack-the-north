import importlib.util
import json
import sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root),str(root/'scripts')]
spec=importlib.util.spec_from_file_location('preview_watch',root/'scripts/robot_sentry_watch.py')
w=importlib.util.module_from_spec(spec);spec.loader.exec_module(w)

def test_preview_failure_debounces_and_records_recovery(monkeypatch,tmp_path):
    clock=[100.0];broken=[True];count=[0]
    monkeypatch.setattr(w.time,'time',lambda:clock[0])
    monkeypatch.setattr(w.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(w,'STATE_FILE',tmp_path/'watch.json')
    monkeypatch.setattr(w.pi_link,'read_env',lambda:{'PI_HOST':'localhost','PI_PORT':'8000'})
    monkeypatch.setattr(w,'sample_telemetry',lambda *a:{'tilt':[0.0],'skew_s':0})
    def get(host,port,path):
        if path=='/healthz':
            count[0]+=1
            return 200,{},json.dumps({'boot_id':'b','cameras':['cam0'],'events':{'last_id':f'b:{count[0]}'}}).encode()
        return (503,{},b'') if broken[0] else (200,{'X-Frame-Age-Ms':'20'},b'\xff\xd8jpeg')
    monkeypatch.setattr(w,'get',get)
    watch=w.Watch(dry=True)
    first=watch.step();assert 'camera_unavailable' in first['pending'];assert not first['filed']
    clock[0]+=11
    failed=watch.step();assert failed['filed'][-1]['kind']=='camera_unavailable'
    clock[0]+=5;watch.step();assert len(watch.filed)==1
    broken[0]=False;clock[0]+=5
    recovered=watch.step();assert recovered['filed'][-1]['kind']=='camera_unavailable_recovered';assert not recovered['open'];assert not watch.preview_error

def test_preview_exception_is_not_swallowed(monkeypatch):
    monkeypatch.setattr(w.pi_link,'read_env',lambda:{'PI_HOST':'localhost'})
    monkeypatch.setattr(w,'sample_telemetry',lambda *a:{'tilt':[0.0],'skew_s':0})
    def get(host,port,path):
        if path=='/healthz':return 200,{},b'{"boot_id":"b","cameras":["cam0"]}'
        raise TimeoutError('timed out')
    monkeypatch.setattr(w,'get',get)
    assert 'TimeoutError' in w.Watch(dry=True).check()['bad']['camera_unavailable']

def test_stale_jpeg_is_not_healthy(monkeypatch):
    monkeypatch.setattr(w.pi_link,'read_env',lambda:{'PI_HOST':'localhost'})
    monkeypatch.setattr(w,'sample_telemetry',lambda *a:{'tilt':[0.0],'skew_s':0})
    monkeypatch.setattr(w,'get',lambda host,port,path:(200,{},b'{"boot_id":"b","cameras":["cam0"]}') if path=='/healthz' else (200,{'x-frame-age-ms':'4000'},b'\xff\xd8jpeg'))
    assert 'frame age 4000 ms' in w.Watch(dry=True).check()['bad']['camera_unavailable']
