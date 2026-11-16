#!/usr/bin/env python3
"""Run the prepared FuzzBench checkout through the rootless Podman API."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys


def environment():
    if os.geteuid() == 0:
        raise RuntimeError('Run as your normal user, without sudo.')
    for executable in ('podman', 'docker', 'make', 'git'):
        if not shutil.which(executable):
            raise RuntimeError(f'Missing {executable}; docker must be the real Docker CLI.')
    info = json.loads(subprocess.check_output(['podman', 'info', '--format=json']))
    if not info['host']['security']['rootless']:
        raise RuntimeError('Podman is not rootless.')
    path = os.environ.get('FUZZBENCH_PODMAN_SOCKET',
                          f'{os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")}'
                          '/podman/podman.sock')
    if not Path(path).is_socket():
        raise RuntimeError(f'No socket at {path}; start systemctl --user start podman.socket')
    connection = socket.socket(socket.AF_UNIX)
    try:
        connection.connect(path)
    finally:
        connection.close()
    env = dict(os.environ, DOCKER_HOST='unix://' + path,
               DOCKER_API_VERSION='1.40', DOCKER_BUILDKIT='0',
               FUZZBENCH_PODMAN_SOCKET=path,
               FUZZBENCH_CPU_LIST=','.join(map(str, sorted(os.sched_getaffinity(0)))),
               PYTHONPATH='.')
    subprocess.run(['docker', 'version'], env=env, check=True)
    return env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['check', 'build', 'run'])
    parser.add_argument('--runners-cpus', type=int, default=112)
    parser.add_argument('--measurers-cpus', type=int, default=8)
    parser.add_argument('--concurrent-builds', type=int, default=4)
    args, extra = parser.parse_known_args()
    if extra[:1] == ['--']:
        extra = extra[1:]
    root = Path(__file__).resolve().parents[1]
    if not (root / 'podman/adaptation.patch').exists():
        parser.error('Run prepare.py first, then use run.py in the prepared checkout.')
    os.chdir(root)
    env = environment()
    if args.action == 'check':
        print('Rootless API reachable. Available CPU IDs:', env['FUZZBENCH_CPU_LIST'])
        return
    if args.action == 'build':
        subprocess.run(['make', '-j1', 'dispatcher-image'], env=env, check=True)
        # Verify both API access from the dispatcher and taskset before spending
        # time building benchmark/fuzzer combinations.
        subprocess.run(['docker', 'run', '--rm', '--security-opt=label=disable',
                        '-v', env['FUZZBENCH_PODMAN_SOCKET'] + ':/var/run/docker.sock',
                        '-e', 'DOCKER_HOST=unix:///var/run/docker.sock',
                        '-e', 'DOCKER_API_VERSION=1.40',
                        'gcr.io/fuzzbench/dispatcher-image', '/bin/bash', '-ec',
                        'docker version; taskset -c ' + env['FUZZBENCH_CPU_LIST'].split(',')[0]
                        + ' true'], env=env, check=True)
        return
    if min(args.runners_cpus, args.measurers_cpus, args.concurrent_builds) <= 0:
        parser.error('CPU and build counts must be positive.')
    if args.runners_cpus + args.measurers_cpus > len(os.sched_getaffinity(0)):
        parser.error('Runner and measurer counts exceed your CPU affinity allocation.')
    if not extra:
        parser.error('Pass experiment arguments after -- (see README.md).')
    command = [sys.executable, 'experiment/run_experiment.py',
               '--allow-uncommitted-changes',
               '--runners-cpus', str(args.runners_cpus),
               '--measurers-cpus', str(args.measurers_cpus),
               '--concurrent-builds', str(args.concurrent_builds)] + extra
    subprocess.run(command, env=env, check=True)


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
