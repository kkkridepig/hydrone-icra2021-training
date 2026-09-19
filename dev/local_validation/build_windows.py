#!/usr/bin/env python3
"""Build the Linux image via Windows when a localhost proxy is unreachable in WSL.

Uses only the Python standard library and the already installed Docker Desktop.
No Python dependencies or torch are installed on the Windows/WSL host.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
from urllib.parse import urlsplit, urlunsplit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cpu-torch', action='store_true')
    parser.add_argument('--ubuntu-mirror', default='', help='Optional HTTPS Ubuntu mirror; APT signature checks stay enabled')
    parser.add_argument('--forward-local-proxy', action='store_true', help='Forward an existing unauthenticated Windows localhost proxy to Docker build steps')
    args = parser.parse_args()
    if os.name != 'nt':
        parser.error('Windows fallback only; use run.bash in Linux/WSL')
    here = Path(__file__).resolve().parent
    docker = shutil.which('docker')
    if docker is None:
        docker = str(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) /
                     'Docker/Docker/resources/bin/docker.exe')
    if not Path(docker).is_file():
        parser.error('Docker Desktop CLI not found')
    environment = os.environ.copy()
    environment['PATH'] = str(Path(docker).parent) + os.pathsep + environment.get('PATH', '')
    for scheme, proxy in urllib.request.getproxies().items():
        if scheme in ('http', 'https'):
            environment.setdefault(scheme.upper() + '_PROXY', proxy)
    target = 'cpu-torch' if args.cpu_torch else 'base'
    image = 'hydrone-local-validation:noetic' + ('-cpu' if args.cpu_torch else '')
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    output = here / '_runs' / (stamp + '-windows-build-' + target)
    output.mkdir(parents=True, exist_ok=False)
    command = [docker, 'build', '--progress=plain', '--target', target, '-t', image, str(here)]
    if args.ubuntu_mirror:
        command[2:2] = ['--build-arg', 'UBUNTU_APT_MIRROR=' + args.ubuntu_mirror]
    if args.forward_local_proxy:
        parsed = urlsplit(urllib.request.getproxies().get('https', ''))
        if parsed.hostname not in ('localhost', '127.0.0.1') or not parsed.port or parsed.username or parsed.password:
            parser.error('Proxy forwarding requires an existing localhost proxy without embedded credentials')
        proxy = urlunsplit((parsed.scheme, 'host.docker.internal:' + str(parsed.port), '', '', ''))
        proxy_arguments = []
        for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy'):
            proxy_arguments.extend(['--build-arg', name + '=' + proxy])
        command[2:2] = proxy_arguments
    recorded_command = [part.split('=', 1)[0] + '=<local proxy>'
                        if part.lower().startswith(('http_proxy=', 'https_proxy=')) else part for part in command]
    metadata = {'command': recorded_command, 'image': image, 'target': target,
                'dockerfile_sha256': hashlib.sha256((here / 'Dockerfile').read_bytes()).hexdigest(),
                'scope': 'Linux Docker image only; no PPU runtime validation'}
    (output / 'build.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
    print('Build log: ' + str(output / 'console.log'), flush=True)
    with (output / 'console.log').open('wb') as log:
        result = subprocess.run(command, env=environment, stdout=log, stderr=subprocess.STDOUT)
    (output / 'exit-code.txt').write_text(str(result.returncode) + '\n', encoding='utf-8')
    print('Build exit code: ' + str(result.returncode))
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
