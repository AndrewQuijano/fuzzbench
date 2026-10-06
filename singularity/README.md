# Running FuzzBench images with Singularity/Apptainer

HPC clusters like NYU's don't allow Docker, only Singularity (or Apptainer, its renamed fork, which
installs a `singularity` command too). The plan:

1. **Build** the FuzzBench images with Docker somewhere you have root (your laptop via WSL2, or CI).
2. **Convert** each image to a `.sif` file.
3. **Run** the `.sif` files with Singularity on the cluster. The cluster never has to build anything.

Sections 1–6 test everything on a Windows laptop (8 cores, 16 GB) in WSL2. Section 7 runs it on
NYU Torch. The code changes are listed at the end.

## 1. Set up WSL2 (Windows → Linux)

Singularity is Linux-only. On Windows it runs inside WSL2.

In PowerShell (as admin):

```powershell
wsl --install -d Ubuntu-22.04   # 22.04 ships Python 3.10, which FuzzBench expects
```

Limit what WSL can use so builds don't freeze Windows. Create `%UserProfile%\.wslconfig`:

```ini
[wsl2]
memory=10GB      # leave ~6 GB for Windows
processors=6     # leave 2 cores for Windows
swap=8GB
```

Then run `wsl --shutdown` and reopen Ubuntu.

**Clone the repo inside WSL** (e.g. `~/fuzzbench`), not under `/mnt/c/...`. Builds on the OneDrive or
Windows filesystem are very slow and have permission problems.

Plan on roughly **40 GB of free disk**. The base, builder and runner images for one fuzzer and one
benchmark come to about 10–15 GB, and each `.sif` adds another 1–3 GB.

## 2. Install Docker and Apptainer in WSL

```bash
# Docker (or turn on Docker Desktop's WSL integration instead)
sudo apt-get update && sudo apt-get install -y docker.io make rsync
sudo usermod -aG docker $USER && newgrp docker

# Apptainer (provides the `singularity` command)
sudo add-apt-repository -y ppa:apptainer/ppa
sudo apt-get update && sudo apt-get install -y apptainer
singularity --version
```

Check which version the cluster has (`singularity --version` on a login node). SIF files built with
Apptainer 1.x generally run on SingularityCE 3.x/4.x.

Test it:

```bash
singularity exec docker://ubuntu:22.04 cat /etc/os-release
```

## 3. Build one fuzzer×benchmark with Docker

Use a small benchmark and a simple fuzzer. `libpng_libpng_read_fuzzer` builds quickly.

```bash
cd ~/fuzzbench
make install-dependencies
export FUZZER=afl BENCHMARK=libpng_libpng_read_fuzzer

make build-$FUZZER-$BENCHMARK          # runner image (builds base + builder images too)
make build-coverage-$BENCHMARK         # coverage image, which the measurer needs
make test-run-$FUZZER-$BENCHMARK       # Docker check: fuzzes for ~20s
```

Don't use `make -j` on this laptop, because clang builds use a lot of memory. On the cluster you'll
only use the output files.

## 4. Convert to SIF

```bash
mkdir -p ~/sifs/runners/$FUZZER ~/sifs/builders/coverage
singularity build ~/sifs/runners/$FUZZER/$BENCHMARK.sif \
    docker-daemon://gcr.io/fuzzbench/runners/$FUZZER/$BENCHMARK:latest
singularity build ~/sifs/builders/coverage/$BENCHMARK.sif \
    docker-daemon://gcr.io/fuzzbench/builders/coverage/$BENCHMARK:latest
```

`docker-daemon://` needs an explicit tag, and `latest` is what local builds produce. To get the files to
the cluster, copy them with `scp`/`rsync`. You can also push the images to a registry (Docker Hub,
GHCR) and run `singularity pull docker://...` on the cluster.

## 5. Smoke test: fuzz under Singularity (works today)

This does the same thing as `make test-run-...`, but with Singularity. Singularity images are
read-only, and FuzzBench's runner writes into `/out`. So the test points the corpus and the working
directory at a writable host folder bound to `/trial`.

```bash
mkdir -p ~/fb-smoke/{corpus,seeds}
cat > ~/fb-smoke/env <<EOF
FUZZ_OUTSIDE_EXPERIMENT=1
FORCE_LOCAL=1
TRIAL_ID=1
FUZZER=$FUZZER
BENCHMARK=$BENCHMARK
FUZZ_TARGET=libpng_read_fuzzer
MAX_TOTAL_TIME=120
SNAPSHOT_PERIOD=30
OUTPUT_CORPUS_DIR=/trial/corpus
SEED_CORPUS_DIR=/trial/seeds
EOF

singularity run \
    --cleanenv --no-home --writable-tmpfs \
    --bind ~/fb-smoke:/trial --pwd /trial \
    --env-file ~/fb-smoke/env \
    ~/sifs/runners/$FUZZER/$BENCHMARK.sif
```

The test passes if AFL's output streams for about 2 minutes and `~/fb-smoke/corpus` fills up. Some
warnings are expected and harmless when you aren't root:
- `nice: cannot set niceness: Permission denied`
- writes to `/proc/sys/kernel/...` failing

Here's why each flag matters. They'll be the same on the cluster:
- `--cleanenv`: stops your host environment (`PYTHONPATH`, SLURM variables, ...) from leaking into
  the container. The image's own `ENV` values (`OUT`, `ROOT_DIR`, `PYTHONPATH=/src`) are still set.
- `--no-home`: stops your dotfiles and home-directory Python packages from shadowing the image's.
- `--writable-tmpfs`: a small (≈64 MB) scratch layer over the image, for fuzzers that write outside
  `/trial`. Large data must go to a bind mount.
- `--pwd /trial`: the runner writes `corpus-archives/` and `results/` to its current directory, and
  Singularity ignores the Dockerfile's `WORKDIR`.

Before moving on, repeat this test for each fuzzer you plan to use. Some fuzzers assume root or a
writable `/out` in ways the AFL family doesn't.

## 6. Full local experiment under Singularity (WSL)

`singularity/build_sifs.sh` does steps 3–4 for every fuzzer×benchmark and also writes the dispatcher
SIF and the coverage archive the measurer needs:

```bash
cd ~/fuzzbench
bash singularity/build_sifs.sh ~/sifs "afl" "libpng_libpng_read_fuzzer"
# -> ~/sifs/dispatcher-image.sif
#    ~/sifs/runners/afl/libpng_libpng_read_fuzzer.sif
#    ~/sifs/coverage/coverage-build-libpng_libpng_read_fuzzer.tar.gz
```

Write `~/fb-exp/experiment-config.yaml` (unquoted absolute paths, the script greps them):

```yaml
trials: 2
max_total_time: 1800        # 30 min, enough to see a report
snapshot_period: 300        # measure every 5 min instead of 15
docker_registry: gcr.io/fuzzbench
experiment_filestore: /home/<you>/fb-exp/data
report_filestore: /home/<you>/fb-exp/report
local_experiment: true
```

Run it (`-a` allows uncommitted changes):

```bash
SIF_DIR=~/sifs bash singularity/run_experiment.sh ~/fb-exp/experiment-config.yaml test-sing -a \
    --fuzzers afl --benchmarks libpng_libpng_read_fuzzer
```

What to check while it runs:
- `ls /tmp/fuzzbench-$USER-test-sing/spool/`: one `*.sh.started` + `*.sh.log` per trial.
- `/tmp/fuzzbench-$USER-test-sing/trials/test-sing/<trial>/runner-log.txt`: AFL output.
- `~/fb-exp/data/test-sing/experiment-folders/`: corpus snapshots appear every 5 min.
- `~/fb-exp/report/test-sing/index.html`: the report (first one after ~1–2 snapshot periods).

The script exits when the dispatcher finishes (after `max_total_time` plus the final measurement).

Don't pass `--runners-cpus`/`--measurers-cpus` on the cluster: they pin to CPU ids starting at 0,
which may be outside the CPUs SLURM gave your job. Locally they're fine.

## 7. On NYU Torch

```bash
# From WSL: copy the repo (same commit!) and the SIFs. Use the data transfer node.
rsync -av --exclude .venv ~/fuzzbench/ <netid>@dtn.torch.hpc.nyu.edu:/scratch/<netid>/fuzzbench/
rsync -av ~/sifs/ <netid>@dtn.torch.hpc.nyu.edu:/scratch/<netid>/sifs/
```

On Torch, write `/scratch/$USER/fb-exp/experiment-config.yaml` (same as above, with
`/scratch/<netid>/fb-exp/...` paths). First try it interactively on a compute node (not the login
node) with a short `max_total_time: 900`:

```bash
srun --account=<your torch_pr_... account> --cpus-per-task=4 --mem=16G --time=01:00:00 --pty bash
cd /scratch/$USER/fuzzbench
SIF_DIR=/scratch/$USER/sifs bash singularity/run_experiment.sh \
    /scratch/$USER/fb-exp/experiment-config.yaml hpc-test -a \
    --fuzzers afl --benchmarks libpng_libpng_read_fuzzer
```

Then edit `singularity/fuzzbench.sbatch` (account, CPUs, memory, time, fuzzers/benchmarks) and
`sbatch singularity/fuzzbench.sbatch`. Don't use the preemption partitions: a requeued job restarts
the experiment from scratch.

## How it works (the code changes)

The Singularity path is on only when `SIF_DIR` is set (by `run_experiment.sh`), so Docker and cloud
experiments behave exactly as before. No `--fakeroot` or nested containers needed.

| Where | Change |
|---|---|
| `singularity/run_experiment.sh` (new) | Runs `run_experiment.py` (with `MANUAL_EXPERIMENT=1`, so it only copies source and config to the filestore) and then the dispatcher inside `dispatcher-image.sif`, mirroring `LocalDispatcher`. Also runs a host-side loop that executes trial startup scripts the dispatcher drops into a spool dir. |
| `common/gcloud.py::run_local_instance` | If `FUZZBENCH_SPOOL_DIR` is set, copy the trial startup script there instead of running it (a container can't start sibling containers; there's no docker.sock). |
| `experiment/resources/runner-startup-script-template.sh` + 1 kwarg in `scheduler.py` | `{% if sif_dir %}`: `singularity run` the runner SIF with the flags from step 5; `--cpuset-cpus` becomes `taskset -c`. |
| `experiment/build/singularity_build.py` (new) + 2 lines in `builder.py` | "Build" backend: checks the runner SIFs exist and copies the prebuilt coverage archive to the filestore. |
| `docker/dispatcher-image/Dockerfile` | Adds `git`, which `run_experiment.py` needs. |
| `singularity/build_sifs.sh`, `singularity/fuzzbench.sbatch` (new) | Build/convert on the laptop; the SLURM job. |

## Cluster facts (checked 2026-09-30)

- Torch has Apptainer 1.5.3 (`singularity` is an alias). `--env-file`, `--no-home` and
  `--writable-tmpfs` all exist. SIFs built by Apptainer 1.3+ on WSL run on it.
- `singularity --fakeroot` errored because `--fakeroot` is an option of `exec`/`run`/`build`, not
  of the top-level command. The correct test is `singularity exec --fakeroot docker://alpine id`
  (it prints `uid=0(root)` if allowed). This setup doesn't need it either way.
- Login and compute nodes run Red Hat; build nothing on login nodes. Jobs must pass `--account`.
