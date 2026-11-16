"""Focused adapter checks against this checkout; needs Jinja2, no engine."""
import ast
import os
from pathlib import Path
import queue
import shutil
import subprocess
import tempfile
import types
import unittest

import jinja2

from prepare import adapt


class AdapterTest(unittest.TestCase):
    def test_setup_without_network_or_package_installation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'podman').mkdir()
            (root / 'podman/adaptation.patch').touch()
            (root / 'requirements.txt').touch()
            setup = Path(__file__).with_name('setup.sh')
            shutil.copy2(setup, root / 'podman/setup.sh')
            fakebin = root / 'fakebin'
            fakebin.mkdir()
            commands = {
                'docker': 'echo "podman version 5"',
                'uname': 'echo x86_64',
                'curl': 'echo download >> "$TEST_LOG"',
                'tar': 'mkdir -p "$4/docker"; printf "#!/bin/sh\\necho Docker version 20.10.24\\n" > "$4/docker/docker"',
            }
            for name, script in commands.items():
                path = fakebin / name
                path.write_text('#!/bin/sh\nset -eu\n' + script + '\n')
                path.chmod(0o755)
            (root / '.venv/bin').mkdir(parents=True)
            python = root / '.venv/bin/python'
            python.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$TEST_LOG"\n')
            python.chmod(0o755)
            env = dict(os.environ, HOME=str(root / 'home'),
                       PATH=str(fakebin) + os.pathsep + os.environ['PATH'],
                       TEST_LOG=str(root / 'calls'))
            for _ in range(2):
                subprocess.run(['bash', str(root / 'podman/setup.sh')], env=env,
                               check=True, capture_output=True, text=True)
            calls = (root / 'calls').read_text()
            self.assertEqual(calls.count('download'), 1)
            self.assertIn('setuptools==75.8.0', calls)
            self.assertIn('import google.cloud.logging', calls)
            self.assertTrue((root / 'home/.local/bin/docker').is_file())

    def test_generated_execution_and_affinity(self):
        source = Path(__file__).resolve().parents[1]
        source = Path(os.environ.get('FUZZBENCH_TEST_SOURCE', str(source)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('experiment', 'docker'):
                shutil.copytree(source / name, root / name)
            shutil.copy2(source / 'requirements.txt', root / 'requirements.txt')
            patch = source / 'podman/adaptation.patch'
            if patch.exists():
                subprocess.run(['git', 'apply', '--reverse', str(patch)],
                               cwd=root, check=True)
            (root / 'podman').mkdir()
            adapt(root)
            # Exercise the generator itself: the Makefile must not re-enable
            # BuildKit after the launcher disables it in the environment.
            generator = ast.parse((root / 'docker/generate_makefile.py').read_text())
            assignment = next(node for node in ast.walk(generator)
                              if isinstance(node, ast.Assign) and
                              any(isinstance(target, ast.Name) and target.id == 'makefile'
                                  for target in node.targets))
            generated_header = ast.literal_eval(assignment.value)
            result = subprocess.run(['make', '-f', '-', 'probe'],
                                    input=generated_header + 'probe:\n\t@echo $(DOCKER_BUILDKIT)\n',
                                    text=True, capture_output=True, check=True)
            self.assertEqual(result.stdout.strip(), '0')
            self.assertIn('setuptools==75.8.0', (root / 'requirements.txt').read_text())
            template = jinja2.Template((root / 'experiment/resources/'
                                       'runner-startup-script-template.sh').read_text())
            local = template.render(local_experiment=True, cpuset='8,10',
                                    docker_image_url='example/runner', num_cpu_cores=2)
            self.assertNotIn('/proc/sys', local)
            self.assertNotIn('--privileged', local)
            self.assertNotIn('--cpus=', local)
            self.assertNotIn('--cpuset-cpus', local)
            self.assertIn('--entrypoint=/usr/bin/taskset', local)
            self.assertIn('example/runner -c 8,10 /bin/bash /src/', local)
            subprocess.run(['bash', '-n'], input=local, text=True, check=True)
            cloud = template.render(local_experiment=False, cpuset='0-1',
                                    docker_image_url='example/runner', num_cpu_cores=2)
            self.assertIn('--privileged --cpus=2', cloud)
            self.assertIn('--cpuset-cpus=0-1', cloud)
            self.assertNotIn('--entrypoint', cloud)

            def load_function(path, name, namespace, stop_before_with=False):
                tree = ast.parse((root / path).read_text())
                function = next(node for node in tree.body
                                if isinstance(node, ast.FunctionDef) and node.name == name)
                if stop_before_with:
                    index = next(i for i, node in enumerate(function.body)
                                 if isinstance(node, ast.With))
                    function.body = function.body[:index] + [
                        ast.Return(value=ast.Name(id='core_allocation', ctx=ast.Load()))]
                module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
                exec(compile(module, path, 'exec'), namespace)
                return namespace[name]

            logger = types.SimpleNamespace(info=lambda *args: None)
            local_utils = types.SimpleNamespace(is_local_experiment=lambda: True)
            env = types.SimpleNamespace(environ={'FUZZBENCH_CPU_LIST': '8,10,12,14,16,18'})
            namespace = dict(os=env, logger=logger, experiment_utils=local_utils,
                             get_experiment_trials=lambda name: types.SimpleNamespace(all=lambda: []))
            schedule = load_function('experiment/scheduler.py', 'schedule_loop',
                                     namespace, True)
            allocation = schedule(dict(experiment='test', runners_cpus=4,
                                       runner_num_cpu_cores=2))
            self.assertEqual(allocation, {'8,10': None, '12,14': None})
            namespace = dict(os=env, logger=logger, experiment_utils=local_utils,
                             multiprocessing=types.SimpleNamespace(Queue=queue.Queue),
                             _process_init=object())
            pool = load_function('experiment/measurer/measure_manager.py',
                                 'get_pool_args', namespace)(2, 4)
            self.assertEqual(pool[0], 2)
            self.assertEqual([pool[2][0].get_nowait() for _ in range(2)], [16, 18])
            # Refuse a second application rather than silently producing a partial patch.
            with self.assertRaises(ValueError):
                adapt(root)


if __name__ == '__main__':
    unittest.main()
