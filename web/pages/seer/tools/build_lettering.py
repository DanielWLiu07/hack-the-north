"""Reuse the landing's exact Katie Roze tracing, writing only Seer-owned assets."""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('landing_trace', HERE.parents[1] / 'landing/tools/build_gitrl.py')
trace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace)
trace.OUT = HERE / 'lettering'
for word in ['SENTRY', 'TELEMETRY']:
    sys.argv = ['build_lettering.py', '--word', word]
    trace.main()
