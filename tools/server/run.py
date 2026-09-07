#!/usr/bin/env python3
"""Portable, isolated training invocation with checked results and saved provenance."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import xmlrpc.client

import numpy as np
import torch
import yaml

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / 'src/hydrone_deep_rl_icra/hydrone_aerial_underwater_deep_rl'
sys.path.insert(0, str(PACKAGE / 'src'))
from hydrone_icra2021.runner_utils import load_yaml_with_base


def finite(value):
    if isinstance(value, torch.Tensor):
        assert torch.isfinite(value).all(), 'nonfinite checkpoint tensor'
    elif isinstance(value, np.ndarray) and np.issubdtype(value.dtype, np.number):
        assert np.isfinite(value).all(), 'nonfinite checkpoint array'
    elif isinstance(value, (float, np.floating)):
        assert np.isfinite(value), 'nonfinite checkpoint scalar'
    elif isinstance(value, dict):
        for item in value.values(): finite(item)
    elif isinstance(value, (tuple, list)):
        for item in value: finite(item)


def main():
    def stop_requested(signum, frame):
        raise KeyboardInterrupt('Training launcher received signal %s' % signum)
    signal.signal(signal.SIGINT, stop_requested)
    signal.signal(signal.SIGTERM, stop_requested)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--algorithm', choices=['ddpg', 'sac'], required=True)
    parser.add_argument('--stage', type=int, choices=[1, 2], required=True)
    parser.add_argument('--seed', type=int, default=0)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--steps', type=int, choices=[1, 100, 1000])
    group.add_argument('--episodes', type=int)
    group.add_argument('--full', action='store_true')
    parser.add_argument('--resume', type=Path, help='Trusted same-stage checkpoint; copied before use')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.episodes is not None and not 1 <= args.episodes <= 10:
        parser.error('short experiments require 1..10 episodes')
    if not (args.steps or args.episodes or args.full): args.steps = 100
    if not torch.cuda.is_available(): raise RuntimeError('CUDA unavailable; check server accelerator compatibility')
    assert sys.version_info[:2] == (3, 8), 'Use Python 3.8'
    # Exercise an actual GPU operation, not merely driver enumeration.
    assert torch.isfinite(torch.randn(32, 32, device='cuda').square().sum()).item()
    old_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(2)
    try:
        master = xmlrpc.client.ServerProxy(os.environ['ROS_MASTER_URI'])
        result = master.getPid('/hydrone_preflight')
    except OSError:
        result = None
    finally:
        socket.setdefaulttimeout(old_timeout)
    if result is not None:
        raise RuntimeError('An ROS master is already running. Stop the previous experiment first.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = (args.output or ROOT / 'artifacts/server' / f'{args.algorithm}_stage{args.stage}_seed{args.seed}_{stamp}').resolve()
    output.mkdir(parents=True, exist_ok=False)
    config = load_yaml_with_base(str(PACKAGE / 'config/icra2021_stage1_formal.yaml'))
    config.pop('base_config', None)
    config.update(stage=args.stage, world=f'stage_{args.stage}', episodes=1000 if args.stage == 1 else 2500, seed=args.seed)
    config['training'].update(device='cuda:0', seed=args.seed)
    config['logging']['output_dir'] = str(output / 'run')
    config['checkpoint'] = {'path': str(output / 'checkpoint.pt'), 'full_resume': True}
    config_path = output / 'config.yaml'
    config_path.write_text(yaml.safe_dump(config, sort_keys=False))
    checkpoint_args = []
    start_episode = 0
    start_global_step = 0
    if args.resume:
        source = args.resume.expanduser().resolve()
        payload = torch.load(source, map_location='cpu', weights_only=False)
        assert payload['stage'] == args.stage and payload['algorithm'] == args.algorithm, 'Cross-stage/algorithm resume is forbidden'
        start_episode = int(payload['episode'])
        start_global_step = int(payload['global_step'])
        if start_episode >= config['episodes']:
            raise ValueError('Checkpoint already reached the configured episode target')
        finite(payload)
        destination = output / 'checkpoint.pt'
        shutil.copy2(source, destination)
        checkpoint_args = ['checkpoint:=' + str(destination)]
    manifest = {'created_at_utc': stamp, 'arguments': vars(args).copy(),
                'torch': torch.__version__, 'cuda': torch.version.cuda, 'python': sys.version,
                'gpu': torch.cuda.get_device_name(0), 'source_sha256': {}}
    for key, value in manifest['arguments'].items():
        if isinstance(value, Path): manifest['arguments'][key] = str(value)
    for path in sorted(PACKAGE.rglob('*')):
        if path.is_file() and path.suffix in ('.py', '.yaml', '.launch', '.xml', '.world') and '__pycache__' not in path.parts:
            manifest['source_sha256'][str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True)
    manifest['git_commit'] = head.stdout.strip() if head.returncode == 0 else None
    if head.returncode == 0:
        manifest['git_status'] = subprocess.check_output(
            ['git', 'status', '--porcelain'], cwd=ROOT, text=True)
    for path in sorted((ROOT / 'tools/server').glob('*')):
        if path.is_file():
            manifest['source_sha256'][str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    (output / 'invocation.json').write_text(json.dumps(manifest, indent=2))
    command = ['roslaunch', 'hydrone_aerial_underwater_deep_rl', 'icra2021_paper.launch',
               f'algorithm:={args.algorithm}', f'stage:={args.stage}',
               'mode:=' + ('resume' if args.resume else 'train'), 'gui:=false',
               'paused:=false', 'run_agent:=true', f'step_limit:={args.steps or 0}',
               f'episode_limit:={args.episodes or 0}', 'config:=' + str(config_path)] + checkpoint_args
    print('OUTPUT=' + str(output), flush=True)
    process = None
    try:
        with (output / 'launch.log').open('w') as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = None if args.full else time.monotonic() + (900 if args.steps else 1800)
            while process.poll() is None:
                if deadline and time.monotonic() > deadline: raise TimeoutError('Bounded experiment exceeded wall-time limit')
                time.sleep(2)
            if process.returncode: raise RuntimeError(f'roslaunch exited {process.returncode}; inspect launch.log')
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGINT)
            try: process.wait(timeout=25)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    suffix = f'step_{args.steps}' if args.steps else f'episode_{args.episodes}' if args.episodes else ''
    summary_path = output / 'run' / 'gates' / suffix / 'summary.json' if suffix else output / 'run/summary.json'
    summary = json.loads(summary_path.read_text())
    assert summary['status'] == 'completed' and not summary['interrupted'] and not summary['error'], summary
    if args.steps: assert summary['total_env_steps'] == args.steps, summary
    if args.episodes:
        expected = min(config['episodes'], start_episode + args.episodes)
        assert summary['completed_training_episodes'] == expected, summary
    payload = torch.load(summary['checkpoint'], map_location='cpu', weights_only=False)
    finite(payload)
    assert payload['algorithm'] == args.algorithm and payload['stage'] == args.stage
    assert payload['episode'] == summary['completed_training_episodes'], 'Stale checkpoint episode'
    assert payload['global_step'] == start_global_step + summary['total_env_steps'], 'Stale checkpoint step'
    assert payload['config']['device'] == 'cuda:0'
    assert payload['observation_contract']['state_dim'] == 26 and payload['action_contract']['dim'] == 3
    if args.steps == 1000: assert payload['update_count'] > 0, 'No gradient updates'
    report = dict(summary, gradient_updates=payload['update_count'], validation='passed')
    (output / 'validation.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)


if __name__ == '__main__': main()
