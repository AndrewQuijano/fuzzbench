#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Module for "building" in Singularity experiments. The images are built with
Docker ahead of time and converted to SIF files by singularity/build_sifs.sh,
so this only checks that those files exist in $SIF_DIR."""

import os
import shutil
import subprocess
from typing import Tuple

from common import logs
from experiment.build import local_build

logger = logs.Logger()  # pylint: disable=invalid-name


def build_base_images() -> Tuple[int, str]:
    """Nothing to build, the runner SIFs already contain the base images."""
    return 0, ''


def build_coverage(benchmark):
    """Copy the coverage build archive made by build_sifs.sh to where the
    measurer expects it."""
    local_build.make_shared_coverage_binaries_dir()
    archive = os.path.join(os.environ['SIF_DIR'], 'coverage',
                           f'coverage-build-{benchmark}.tar.gz')
    shutil.copy(archive, local_build.get_shared_coverage_binaries_dir())


def build_fuzzer_benchmark(fuzzer: str, benchmark: str) -> bool:
    """Check that the runner SIF for |fuzzer| and |benchmark| exists."""
    sif = os.path.join(os.environ['SIF_DIR'], 'runners', fuzzer,
                       f'{benchmark}.sif')
    if not os.path.exists(sif):
        logger.error('Runner SIF not found: %s.', sif)
        raise subprocess.CalledProcessError(1, ['test', '-f', sif])
    return True
