"""Run this reviewed copy with the existing local model resources."""
import argparse
import os
from pathlib import Path
import runpy
import socket
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=8892)
args = parser.parse_args()
if not 1024 <= args.port <= 65535:
    parser.error('port must be between 1024 and 65535')
with socket.socket() as probe:
    if probe.connect_ex(('127.0.0.1', args.port)) == 0:
        parser.error(f'port {args.port} is occupied; the existing process will not be stopped')
root = Path(__file__).resolve().parent
runtime = root / 'runtime'
(runtime / 'tmp').mkdir(parents=True, exist_ok=True)
resources = Path(os.environ.get('PIANOFLOW_RESOURCE_ROOT', r'D:\Program Files\pianoflow'))
settings = {
    'NUMBA_CACHE_DIR': str(runtime / 'numba-cache'),
    'TEMP': str(runtime / 'tmp'), 'TMP': str(runtime / 'tmp'),
    'PYTHONDONTWRITEBYTECODE': '1',
    'PIANOFLOW_CACHE_DIR': str(runtime / 'audio'),
    'PIANOFLOW_LOG_DIR': str(runtime / 'logs'),
    'PIANOFLOW_RESOURCE_ROOT': str(resources),
    'PIANOFLOW_BASIC_PITCH_MODEL_PATH': str(resources / 'models/basic_pitch/nmp.onnx'),
    'PIANOFLOW_ML_PYTHON': r'D:\conda_envs\pop2piano\python.exe',
    'PIANOFLOW_SEPARATOR_PYTHON': r'D:\conda_envs\transkun\python.exe',
    'PIANOFLOW_DEMUCS_CHECKPOINT': str(resources / 'models/demucs/htdemucs_6s.th'),
    'PIANOFLOW_FLUIDSYNTH_LOCATION': str(resources / 'tools/fluidsynth/bin'),
    'PIANOFLOW_SOUNDFONT_PATH': str(resources / 'models/soundfonts/MuseScore_General.sf2'),
}
for name, value in settings.items():
    os.environ.setdefault(name, value)
sys.argv = [str(root / 'backend_python/app.py'), '--port', str(args.port)]
print(f'PianoFlow: http://127.0.0.1:{args.port}/', flush=True)
runpy.run_path(sys.argv[0], run_name='__main__')
