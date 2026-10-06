#!/bin/bash
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

# Builds everything a Singularity experiment needs, on a machine with Docker
# and Apptainer/Singularity (e.g. WSL2). Output layout, which
# experiment/build/singularity_build.py and the runner startup script expect:
#   SIF_DIR/dispatcher-image.sif
#   SIF_DIR/runners/FUZZER/BENCHMARK.sif
#   SIF_DIR/coverage/coverage-build-BENCHMARK.tar.gz
#
# Usage: singularity/build_sifs.sh SIF_DIR "FUZZER..." "BENCHMARK..."

set -euo pipefail

if [ $# -ne 3 ]; then
  echo "Usage: $0 SIF_DIR \"FUZZER...\" \"BENCHMARK...\"" >&2
  exit 1
fi
SIF_DIR=$(realpath -m "$1")
FUZZERS=$2
BENCHMARKS=$3
REGISTRY=gcr.io/fuzzbench

cd "$(dirname "$0")/.."
mkdir -p "$SIF_DIR/coverage" "$SIF_DIR/runners"

make dispatcher-image
singularity build --force "$SIF_DIR/dispatcher-image.sif" \
  "docker-daemon://$REGISTRY/dispatcher-image:latest"

for benchmark in $BENCHMARKS; do
  make "build-coverage-$benchmark"
  # Same archive local_build.py::copy_coverage_binaries makes.
  docker run --rm -v "$SIF_DIR/coverage:/host-out" \
    "$REGISTRY/builders/coverage/$benchmark" /bin/bash -c \
    "cd /out; tar -czf /host-out/coverage-build-$benchmark.tar.gz * /src /work"

  for fuzzer in $FUZZERS; do
    make "build-$fuzzer-$benchmark"
    mkdir -p "$SIF_DIR/runners/$fuzzer"
    singularity build --force "$SIF_DIR/runners/$fuzzer/$benchmark.sif" \
      "docker-daemon://$REGISTRY/runners/$fuzzer/$benchmark:latest"
  done
done

echo "Done. Copy $SIF_DIR to the cluster, e.g.:"
echo "  rsync -av $SIF_DIR/ <netid>@dtn.torch.hpc.nyu.edu:/scratch/<netid>/sifs/"
