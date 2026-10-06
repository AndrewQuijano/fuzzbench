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

# Runs a whole FuzzBench experiment (dispatcher + all trials) on one NYU Torch
# node. Edit the account, resources and the variables below, then:
#   sbatch singularity/fuzzbench.sbatch
#
# Size --cpus-per-task as (#fuzzers x #benchmarks x trials) + 2 for the
# dispatcher/measurer, and --time as max_total_time plus ~1-2 hours.

#SBATCH --job-name=fuzzbench
#SBATCH --account=torch_pr_CHANGEME
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=04:00:00
#SBATCH --output=%x-%j.log

set -euo pipefail

export SIF_DIR=/scratch/$USER/sifs
FUZZBENCH_DIR=/scratch/$USER/fuzzbench
CONFIG=/scratch/$USER/fb-exp/experiment-config.yaml
NAME=hpc-$SLURM_JOB_ID

cd "$FUZZBENCH_DIR"
singularity --version
bash singularity/run_experiment.sh "$CONFIG" "$NAME" -a \
  --fuzzers afl \
  --benchmarks libpng_libpng_read_fuzzer
