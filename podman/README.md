# Rootless Podman adapter for local FuzzBench

This adapter prepares a **separate checkout** and changes only that copy. The
original Docker workflow stays intact. `podman/adaptation.patch` in the prepared
checkout records the exact changes. Preparation fails if expected upstream code
has changed. No daemon or experiment is started by preparation.

Status: preparation, generated shell syntax, sparse CPU scheduling and source
syntax are tested locally. **A complete Podman experiment has not been tested.**
Run the small experiment below on the server before starting a campaign.

## Server prerequisites

Use Linux, rootless Podman, Git, Make, curl, and Python **3.9 or 3.10** with venv support.
This FuzzBench revision pins old packages, including NumPy and pandas; newer
Python versions may not install them. Install the **real Docker CLI** as well:
it is an API client here, with no Docker daemon. Use a CLI that supports API
1.40 (for example Docker CLI 20.10 through 28; newer CLIs may raise their minimum).
A `docker` alias or the
`podman-docker` shim is not sufficient. `setup.sh` downloads Docker CLI 20.10.24
to `~/.local/bin/docker` if a real Docker client is missing. It installs only
the client. The dispatcher gets its own Docker CLI.

Podman's Docker-compatible API lets FuzzBench retain its existing build code.
The launcher fixes the API version at 1.40 and disables BuildKit. Both host and
dispatcher connect to the same user's rootless Podman service. The dispatcher
and trials are sibling containers, not nested container engines.

Start the socket as your normal user:

```bash
systemctl --user start podman.socket
```

Without user systemd, keep this running in a separate persistent session:

```bash
mkdir -p "$XDG_RUNTIME_DIR/podman"
podman system service --time=0 "unix://$XDG_RUNTIME_DIR/podman/podman.sock"
```

An alternative socket can be selected with `FUZZBENCH_PODMAN_SOCKET=/absolute/path`.
Use a local Unix socket. A long campaign also needs a persistent login/session
and a user service that survives it; coordinate lingering with the server admin
if needed. Keep Podman storage on a sufficiently large supported local filesystem
(not an NFS home directory). Builds can consume substantial disk and RAM.

## Prepare and build

From the original checkout on the server:

```bash
cd ~/fuzzbench
python3 podman/prepare.py ~/fuzzbench-rootless
cd ~/fuzzbench-rootless
git diff                        # review the isolated changes
bash podman/setup.sh
source .venv/bin/activate
python podman/run.py check
python podman/run.py build
```

`setup.sh` reuses an existing `.venv`, or creates one using Python 3.10 then 3.9
when available. Select an interpreter with `PYTHON=python3.10 bash podman/setup.sh`.
It pins setuptools to 75.8.0 and checks the old Google logging imports. Prepared
requirements also pin setuptools for container builds and Make-created venvs.
The launcher automatically includes `~/.local/bin` in its PATH; no shell profile
edit is needed. To use the downloaded client directly, run
`export PATH="$HOME/.local/bin:$PATH"` in your shell.

The adapter disables BuildKit in the Makefile generator and passes
`DOCKER_BUILDKIT=0` to host and dispatcher Make commands. An environment variable
alone is insufficient because upstream's generated Makefile overrides it.
`run` checks for the dispatcher image and tells you to build it if missing.

If you already prepared a checkout with an older adapter, prepare a **new**
directory (for example `~/fuzzbench-rootless-v2`) from the updated original
checkout and run setup/build there. Existing prepared directories are never
overwritten. Keep using the same external data paths with a new experiment name.

`build` builds the base and dispatcher images, then probes Docker API access
from inside the dispatcher and tests `taskset`. Benchmark/fuzzer images are built
when the experiment starts. Image names remain `gcr.io/fuzzbench/...` because
upstream hardcodes these parent names. Local builds do not push them to a registry.
Use a dedicated Podman user/store if you need independent FuzzBench versions.

## First experiment

Store results **outside both checkouts** so FuzzBench's source archive does not
include experiment data. Use absolute paths without spaces. From the prepared
checkout, with the venv active:

```bash
mkdir -p "$HOME/fuzzbench-data/experiments" "$HOME/fuzzbench-data/reports"
cat > /tmp/fuzzbench-smoke.yaml <<EOF
trials: 1
max_total_time: 600
snapshot_period: 60
docker_registry: gcr.io/fuzzbench
experiment_filestore: $HOME/fuzzbench-data/experiments
report_filestore: $HOME/fuzzbench-data/reports
local_experiment: true
EOF

python podman/run.py run --runners-cpus 2 --measurers-cpus 1 \
  --concurrent-builds 1 -- \
  --experiment-config /tmp/fuzzbench-smoke.yaml \
  --experiment-name podman-smoke-01 \
  --benchmarks freetype2_ftfuzzer --fuzzers afl libfuzzer
```

Use a new experiment name for each attempt. Run in tmux or a persistent job
allocation. The smoke run takes longer than ten minutes because it first builds
images. Confirm successful builds, fuzzer execution, coverage snapshots, and a
report at `~/fuzzbench-data/reports/podman-smoke-01/index.html`. A finished command
alone is insufficient: upstream can omit fuzzer/benchmark pairs whose builds fail.
The dispatcher opens a shell on failure; inspect its output and exit the shell.

## Scale to the server

Copy the smoke config to a permanent config outside the checkout, change
`trials` to 10 and `max_total_time` to 86400 (24 hours per trial), and use:

```bash
python podman/run.py run --runners-cpus 112 --measurers-cpus 8 \
  --concurrent-builds 4 -- \
  --experiment-config "$HOME/fuzzbench-campaign.yaml" \
  --experiment-name podman-campaign-01 \
  --benchmarks freetype2_ftfuzzer bloaty_fuzz_target \
  --fuzzers afl libfuzzer
```

This budgets up to 112 one-CPU trials, 8 measurement workers, and leaves 8 of
128 available logical CPUs out of those allocations. Actual concurrency is
bounded by pending trials: 2 fuzzers × 2 benchmarks × 10 trials is only 40 trials.
Build parallelism is separate; start at 4 and adjust for RAM. FuzzBench does not
apply per-trial memory limits here, so memory can limit concurrency before CPUs.

CPU counts are **logical CPUs**, not necessarily physical cores. Inspect
`lscpu -e=CPU,CORE,SOCKET` before choosing an allocation. The launcher records
the invoking process's allowed CPU IDs and maps runner/measurer assignments
onto that list, including sparse allocations. You can run the launcher under
`taskset -c ...` to choose the allocation. Trials use `taskset` inside the
container instead of rootless CPU controllers; measurement workers use process
affinity. Affinity is not exclusive reservation or a CPU quota. These counts
do not constrain image compilation or all dispatcher/report work. Avoid sharing
physical cores with other campaigns when measuring fuzzer performance.

## Adaptation details and limits

- Mounts the actual user Podman socket at the dispatcher's expected Docker path
  and passes API settings into the dispatcher.
- Extracts the source archive with `tar --no-same-owner` so host numeric UIDs
  outside the rootless user namespace do not cause dispatcher startup to fail.
- Disables SELinux container labeling for dispatcher and shared-volume helper/
  trial containers, as needed for the socket and sibling bind mounts.
- Removes local privileged mode and writes to host `ptrace_scope`/`core_pattern`.
  Retains trial ptrace/nice capabilities and unconfined seccomp from upstream.
- Installs `taskset` in trial images and launches the original runner through it.
- Keeps upstream cloud workflow out of scope. Run experiments through this
  launcher so required CPU and socket variables are present.

Rootless containers cannot change host kernel settings or grant host privileges.
Fuzzers needing those may require an admin or may be unsuitable. The included
AFL already skips its core-handler check; verify crash capture with a known
crashing input before relying on crash comparisons. The smoke test does not
establish support for every fuzzer or benchmark.

FuzzBench uses a fixed `dispatcher-container` name and shared local database;
run one experiment at a time per user/store and data directory. Interrupting
the dispatcher may leave sibling trials alive. Inspect `podman ps` and stop
only containers belonging to your experiment before retrying. Do not use a
global container prune on a shared account.

Reference: [Podman service documentation](https://docs.podman.io/en/stable/markdown/podman-system-service.1.html)
describes the Docker API, user socket, and container socket access requirements.

Adapter checks (no container engine needed):

```bash
python podman/test_adapter.py
```
