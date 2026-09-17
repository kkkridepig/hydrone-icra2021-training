#!/usr/bin/env python3
"""One diagnostic batch: validation screening followed by fresh-seed testing."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import traceback
from diagnostic_core import LEGACY, load_protocol
from core import canonical_hash, json_write, load_config
import run as legacy_run
from diagnostic_report import make_report

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def validate_source(source, c, current_sources):
    report = json.loads((source/'report.json').read_text())
    if (not report.get('execution_complete') or report.get('actual_episodes') != report.get('expected_episodes')
            or report.get('actual_episodes') != 120):
        raise RuntimeError('Source phase2 must be the completed 120-episode experiment')
    old = json.loads((source/'provenance.json').read_text())
    if old.get('phase') != 'phase2' or old.get('config_sha256') != canonical_hash(c):
        raise RuntimeError('Source phase2/config identity mismatch')
    if old.get('source_sha256') != current_sources:
        before = old.get('source_sha256', {})
        changed = [k for k in sorted(set(before)|set(current_sources)) if before.get(k)!=current_sources.get(k)]
        raise RuntimeError('Sources/build changed since successful phase2; preserve original code. Changed: '+str(changed))
    if c['model_seeds'] != [0]:
        raise RuntimeError('This diagnostic expects the successful model seed 0')
    checkpoint = source/'models/seed_0/model.pt'
    if not checkpoint.is_file():
        raise RuntimeError('Missing original model.pt; evidence.zip does not contain model weights')
    return checkpoint


def simulate(out, c, p, port):
    legacy_run.port_free(port)
    legacy_run.port_free(port+30)
    env = os.environ.copy()
    env.update(ROS_MASTER_URI='http://127.0.0.1:%d' % port, ROS_IP='127.0.0.1', ROS_NAMESPACE='/',
               GAZEBO_MASTER_URI='http://127.0.0.1:%d' % (port+30), ROS_LOG_DIR=str(out/'ros_logs'),
               ROS_HOME=str(out/'ros_home'), LIBGL_ALWAYS_SOFTWARE='1', PYTHONUNBUFFERED='1')
    (out/'ros_logs').mkdir()
    (out/'ros_home').mkdir()
    legacy_run.preflight(out, c, env)
    launch = ['roslaunch', '--port', str(port), str(LEGACY/'assets/simulation.launch'),
              'assets:='+str(LEGACY/'assets'), 'namespace:='+c['namespace']]
    if not env.get('DISPLAY'):
        if not shutil.which('xvfb-run') or not shutil.which('xauth'):
            raise RuntimeError('Existing RGB setup needs xvfb-run and xauth')
        launch = ['xvfb-run', '-a', '-s', '-screen 0 640x480x24']+launch
    worker = [sys.executable, str(HERE/'diagnostic_worker.py'), '--output', str(out)]
    sim = agent = None
    deadline = time.monotonic()+p['maximum_wall_hours']*3600
    try:
        with (out/'gazebo.log').open('w') as simlog, (out/'worker.log').open('w') as runlog:
            sim = subprocess.Popen(launch, env=env, stdout=simlog, stderr=subprocess.STDOUT, start_new_session=True)
            agent = subprocess.Popen(worker, env=env, stdout=runlog, stderr=subprocess.STDOUT, start_new_session=True)
            while agent.poll() is None:
                if sim.poll() is not None:
                    raise RuntimeError('Simulation launcher exited; inspect gazebo.log')
                if time.monotonic() > deadline:
                    raise TimeoutError('Diagnostic exceeded wall-time bound')
                if time.time()-(out/'worker.log').stat().st_mtime > 300:
                    raise TimeoutError('No startup/episode heartbeat for 300 seconds')
                time.sleep(.5)
            if agent.returncode != 0:
                raise RuntimeError('Worker failed (%d); inspect worker.log' % agent.returncode)
    finally:
        legacy_run.stop_group(agent)
        legacy_run.stop_group(sim)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-phase2', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--protocol', type=Path, default=HERE/'protocol.json')
    parser.add_argument('--port', type=int, default=11431)
    a = parser.parse_args()
    if not 1024 <= a.port <= 65505:
        parser.error('port must be 1024..65505')
    p = json.loads(a.protocol.read_text())
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    json_write(out/'protocol.json', p)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    error = None
    print('OUTPUT='+str(out), flush=True)
    try:
        source = a.from_phase2.resolve()
        c = load_config(source/'config.json')
        p = load_protocol(a.protocol, c)
        json_write(out/'config.json', c)
        manifest = legacy_run.provenance(c)
        checkpoint = validate_source(source, c, manifest['source_sha256'])
        manifest.update(schema=1, from_phase2=str(source), protocol_sha256=canonical_hash(p),
                        source_report_sha256=legacy_run.sha(source/'report.json'),
                        source_provenance_sha256=legacy_run.sha(source/'provenance.json'),
                        checkpoint_sha256=legacy_run.sha(checkpoint), inference_device='cpu', training=False,
                        diagnostic_sources={str(f.relative_to(ROOT)): legacy_run.sha(f)
                            for f in sorted(HERE.rglob('*')) if f.is_file() and f.suffix in ('.py', '.json', '.bash')})
        manifest['system_plugin_sha256'] = {str(f): legacy_run.sha(f) for f in [
            Path('/opt/ros/noetic/lib/libgazebo_ros_api_plugin.so'),
            Path('/opt/ros/noetic/lib/libgazebo_ros_camera.so')] if f.is_file()}
        json_write(out/'provenance.json', manifest)
        (out/'frozen').mkdir()
        shutil.copy2(checkpoint, out/'frozen/model.pt')
        if legacy_run.sha(out/'frozen/model.pt') != manifest['checkpoint_sha256']:
            raise RuntimeError('Checkpoint copy hash mismatch')
        print('Frozen model and source identity verified; starting diagnostic', flush=True)
        simulate(out, c, p, a.port)
        if (legacy_run.sha(checkpoint) != manifest['checkpoint_sha256']
                or legacy_run.sha(out/'frozen/model.pt') != manifest['checkpoint_sha256']):
            raise RuntimeError('Checkpoint changed during diagnostic')
    except BaseException as exc:
        error = repr(exc)
        (out/'ERROR.txt').write_text(traceback.format_exc())
        print('ERROR: '+error, file=sys.stderr, flush=True)
    finally:
        report = make_report(out, p, error)
        json_write(out/'evidence_manifest.json', {str(f.relative_to(out)): legacy_run.sha(f)
                   for f in sorted((out/'episodes').glob('*')) if f.is_file()})
        evidence = legacy_run.bundle(out)
        print('DIAGNOSIS='+report['diagnosis'], flush=True)
        print('REPORT='+str(out/'REPORT.md'), flush=True)
        print('EVIDENCE='+str(evidence), flush=True)
    return 0 if report['execution_complete'] else 3 if report['screening_complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
