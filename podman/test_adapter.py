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
    def test_generated_execution_and_affinity(self):
        source = Path(__file__).resolve().parents[1]
        source = Path(os.environ.get('FUZZBENCH_TEST_SOURCE', str(source)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('experiment', 'docker'):
                shutil.copytree(source / name, root / name)
            patch = source / 'podman/adaptation.patch'
            if patch.exists():
                subprocess.run(['git', 'apply', '--reverse', str(patch)],
                               cwd=root, check=True)
            (root / 'podman').mkdir()
            adapt(root)
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
