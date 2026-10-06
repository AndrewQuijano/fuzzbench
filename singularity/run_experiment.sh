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

# Runs a local FuzzBench experiment with Singularity/Apptainer instead of
# Docker, all on this machine. This does what run_experiment.py's
# LocalDispatcher does, plus a host-side loop that starts the trial containers
# the dispatcher asks for (a container can't start other containers).
#
# Usage:
#   SIF_DIR=/path/to/sifs singularity/run_experiment.sh CONFIG NAME \
#       [run_experiment.py args, e.g. -a --fuzzers afl --benchmarks ...]

set -euo pipefail

if [ $# -lt 2 ]; then
  echo "Usage: SIF_DIR=... $0 CONFIG NAME [run_experiment.py args...]" >&2
  exit 1
fi
CONFIG=$(realpath "$1")
NAME=$2
shift 2
SIF_DIR=$(realpath "${SIF_DIR:?Set SIF_DIR to the directory build_sifs.sh wrote}")
FUZZBENCH_DIR=$(realpath "$(dirname "$0")/..")
DISPATCHER_SIF=$SIF_DIR/dispatcher-image.sif

yaml_value() {
  grep "^$1:" "$2" | head -n1 | awk '{print $2}'
}
EXPERIMENT_FILESTORE=$(yaml_value experiment_filestore "$CONFIG")
REPORT_FILESTORE=$(yaml_value report_filestore "$CONFIG")
mkdir -p "$EXPERIMENT_FILESTORE" "$REPORT_FILESTORE"

# Fast node-local disk for the dispatcher's /work, the trial directories and
# the spool of trial startup scripts.
SCRATCH=${TMPDIR:-/tmp}/fuzzbench-$USER-$NAME
rm -rf "$SCRATCH"
mkdir -p "$SCRATCH/config" "$SCRATCH/work" "$SCRATCH/spool" "$SCRATCH/trials"
# Copy the config so only it (not its whole directory) goes to the filestore.
cp "$CONFIG" "$SCRATCH/config/experiment.yaml"
export FUZZBENCH_TRIALS_DIR=$SCRATCH/trials

in_dispatcher() {
  singularity exec --cleanenv --no-home --writable-tmpfs \
    --bind "$FUZZBENCH_DIR" --bind "$SIF_DIR" --bind "$SCRATCH" \
    --bind "$SCRATCH/work:/work" \
    --bind "$EXPERIMENT_FILESTORE" --bind "$REPORT_FILESTORE" \
    "$@"
}

echo "== Copying source and config to the experiment filestore."
in_dispatcher --pwd "$FUZZBENCH_DIR" "$DISPATCHER_SIF" \
  env MANUAL_EXPERIMENT=1 PYTHONPATH="$FUZZBENCH_DIR" \
  python3 experiment/run_experiment.py \
  --experiment-config "$SCRATCH/config/experiment.yaml" \
  --experiment-name "$NAME" "$@"

# run_experiment.py fills in defaults, so read the values from its output.
FINAL_CONFIG=$EXPERIMENT_FILESTORE/$NAME/input/config/experiment.yaml
cat > "$SCRATCH/dispatcher.env" <<EOF
LOCAL_EXPERIMENT=True
INSTANCE_NAME=d-$NAME
EXPERIMENT=$NAME
SQL_DATABASE_URL='sqlite:///$EXPERIMENT_FILESTORE/local.db?check_same_thread=False'
EXPERIMENT_FILESTORE=$EXPERIMENT_FILESTORE
REPORT_FILESTORE=$REPORT_FILESTORE
SNAPSHOT_PERIOD=$(yaml_value snapshot_period "$FINAL_CONFIG")
DOCKER_REGISTRY=$(yaml_value docker_registry "$FINAL_CONFIG")
CONCURRENT_BUILDS=4
WORKER_POOL_NAME=
SIF_DIR=$SIF_DIR
FUZZBENCH_SPOOL_DIR=$SCRATCH/spool
EOF

echo "== Starting trial launcher (logs in $SCRATCH/spool)."
(
  while true; do
    for script in "$SCRATCH"/spool/*.sh; do
      [ -e "$script" ] || continue
      mv "$script" "$script.started"
      bash "$script.started" > "$script.log" 2>&1 &
    done
    sleep 5
  done
) &
LAUNCHER_PID=$!
trap 'kill $LAUNCHER_PID 2>/dev/null' EXIT

echo "== Starting dispatcher. Report: $REPORT_FILESTORE/$NAME/index.html"
# Same command LocalDispatcher runs in its Docker container.
in_dispatcher --pwd /work --env-file "$SCRATCH/dispatcher.env" \
  "$DISPATCHER_SIF" /bin/bash -c \
  'rsync -r "${EXPERIMENT_FILESTORE}/${EXPERIMENT}/input/" ${WORK} && '\
'mkdir ${WORK}/src && '\
'tar -xzf ${WORK}/src.tar.gz -C ${WORK}/src && '\
'PYTHONPATH=${WORK}/src python3 ${WORK}/src/experiment/dispatcher.py'
