#!/usr/bin/env python3
"""Prepare an isolated rootless-Podman checkout; never edit the source tree."""
import argparse
import ast
import difflib
import pathlib
import shutil
import subprocess


def adapt(root):
    changes = {}

    def replace(name, before, after):
        path = root / name
        text = changes.get(name, path.read_text())
        if text.count(before) != 1:
            raise ValueError(f'{name}: expected exactly one occurrence of {before!r}')
        changes[name] = text.replace(before, after)

    replace('docker/generate_makefile.py',
            'export DOCKER_BUILDKIT := 1', 'export DOCKER_BUILDKIT := 0')
    # Pin in the prepared requirements too: base/dispatcher images and Make's
    # virtualenv must retain pkg_resources, not just the host's virtualenv.
    replace('requirements.txt', 'alembic==1.8.1',
            'setuptools==75.8.0\nalembic==1.8.1')
    replace('experiment/build/local_build.py',
            "command = ['make', '-j'] + targets",
            "command = ['make', 'DOCKER_BUILDKIT=0', '-j'] + targets")
    replace('experiment/run_experiment.py',
            "'/var/run/docker.sock:/var/run/docker.sock',",
            "os.environ['FUZZBENCH_PODMAN_SOCKET'] + "
            "':/var/run/docker.sock',\n"
            "            '--security-opt=label=disable',\n"
            "            '-e', 'DOCKER_HOST=unix:///var/run/docker.sock',\n"
            "            '-e', 'DOCKER_API_VERSION=1.40',\n"
            "            '-e', 'DOCKER_BUILDKIT=0',\n"
            "            '-e', 'FUZZBENCH_CPU_LIST=' + os.environ['FUZZBENCH_CPU_LIST'],")
    replace('experiment/run_experiment.py', "            '-ti',", "            '-i',")
    replace('docker/dispatcher-image/Dockerfile',
            'DOCKER_VERSION=18.09.7', 'DOCKER_VERSION=20.10.24')
    # Shared bind mounts need to be accessible to sibling containers on SELinux hosts.
    replace('experiment/build/local_build.py',
            "'docker', 'run', '-v', mount_arg, builder_image_url, '/bin/bash', '-c',",
            "'docker', 'run', '--security-opt=label=disable', '-v', mount_arg, "
            "builder_image_url, '/bin/bash', '-c',")

    template = 'experiment/resources/runner-startup-script-template.sh'
    replace(template, '# Make everything ptrace-able.',
            '{% if not local_experiment %}\n# Make everything ptrace-able.')
    replace(template, '# Start docker.', '{% endif %}\n# Start docker.')
    replace(template, '--privileged --cpus={{num_cpu_cores}} --rm',
            '{% if not local_experiment %}--privileged --cpus={{num_cpu_cores}} '
            '{% endif %}--rm')
    replace(template, '{% if cpuset %}--cpuset-cpus={{cpuset}} {% endif %}',
            '{% if local_experiment %}--security-opt=label=disable '
            '--entrypoint=/usr/bin/taskset '
            '{% elif cpuset %}--cpuset-cpus={{cpuset}} {% endif %}')
    replace(template, '{{docker_image_url}} 2>&1',
            '{{docker_image_url}} {% if local_experiment %}-c {{cpuset}} '
            '/bin/bash /src/docker/benchmark-runner/startup-runner.sh {% endif %}2>&1')
    replace('docker/benchmark-runner/Dockerfile',
            '    libglib2.0-0', '    util-linux \\\n    libglib2.0-0')

    # CPU IDs can be sparse under batch schedulers. Carry the host allocation
    # into the dispatcher rather than assuming CPUs start at zero.
    replace('experiment/scheduler.py',
            "            core_allocation = {}\n",
            "            cpu_ids = [int(cpu) for cpu in\n"
            "                       os.environ['FUZZBENCH_CPU_LIST'].split(',')]\n"
            "            core_allocation = {}\n")
    replace('experiment/scheduler.py',
            "                    f'{cpu}-{cpu + runner_num_cpu_cores - 1}'] = None",
            "                    ','.join(str(i) for i in\n"
            "                             cpu_ids[cpu:cpu + runner_num_cpu_cores])] = None")
    replace('experiment/measurer/measure_manager.py',
            '    for cpu in range(runners_cpus, runners_cpus + measurers_cpus):',
            "    cpu_ids = [int(cpu) for cpu in\n"
            "               os.environ['FUZZBENCH_CPU_LIST'].split(',')]\n"
            '    for cpu in cpu_ids[runners_cpus:runners_cpus + measurers_cpus]:')
    # Validate everything before writing even the isolated copy.
    for name, text in changes.items():
        if name.endswith('.py'):
            ast.parse(text, filename=name)
    diff = ''
    for name, text in changes.items():
        path = root / name
        diff += ''.join(difflib.unified_diff(path.read_text().splitlines(True),
                                           text.splitlines(True),
                                           fromfile='a/' + name, tofile='b/' + name))
        path.write_text(text)
    (root / 'podman' / 'adaptation.patch').write_text(diff)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=pathlib.Path)
    parser.add_argument('--source', type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    source = args.source.resolve()
    destination = args.destination.resolve()
    if destination.exists() or destination == source or source in destination.parents:
        parser.error('destination must be a new directory outside the source checkout')
    subprocess.run(['git', 'clone', '--no-hardlinks', '--local', str(source),
                    str(destination)], check=True)
    # Include current working changes, while retaining clone provenance.
    shutil.copytree(source, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('.git', '.venv', '__pycache__',
                                                 '.pytest_cache', 'generated.mk',
                                                 'src.tar.gz'))
    (destination / 'podman').mkdir(exist_ok=True)
    adapt(destination)
    print(f'Prepared {destination}; review podman/adaptation.patch there.')


if __name__ == '__main__':
    main()
