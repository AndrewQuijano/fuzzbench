# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FuzzBench is a service for benchmarking fuzzers against real-world (OSS-Fuzz) benchmarks. It builds
every fuzzer × benchmark combination as Docker images, runs many timed trials, periodically measures
coverage/crashes from the trials' corpora, and generates statistical HTML reports. Everything is Python
3.10 plus a lot of Docker; it's Linux-only in practice (the Makefile uses `/bin/bash` and
`.venv/bin/activate`), so on Windows, file edits are fine but builds and tests need Linux, WSL, or CI.

Full docs live in `docs/` (Jekyll site published at google.github.io/fuzzbench). The most useful ones are
`docs/getting-started/adding_a_new_fuzzer.md`, `docs/developing-fuzzbench/adding_a_new_benchmark.md`,
and `docs/running-a-local-experiment/running_a_local_experiment.md`.

## Commands

All `make` targets create/use `.venv` from `requirements.txt` automatically (`make install-dependencies`).

- `make presubmit`: runs every default check on files changed vs. `origin/master`: license header,
  yapf format check, pylint, pytype, pytest, and fuzzer/benchmark name validation. CI runs
  `FUZZBENCH_TEST_INTEGRATION=1 make presubmit`.
- Single checks: `make format` (yapf in-place), `make lint`, `make typecheck`, `make licensecheck`,
  `make test`. Or call `python3 presubmit.py <check> [--all-files]`. Without `--all-files` they only
  look at files changed vs. master (via `src_analysis/diff_utils.py`).
- Run one test: `source .venv/bin/activate && python3 -m pytest -vv path/to/test_file.py::test_name`.
  Tests sit next to the code as `test_*.py`. The root `conftest.py` forces `SQL_DATABASE_URL=sqlite://`
  (in-memory) and provides the `db`, `environ`, `experiment`, `use_local_filestore` and `use_gsutil`
  fixtures. Mark slow tests with `@pytest.mark.slow`.
- Integration tests that need Docker or GCB are gated by env vars such as `FUZZBENCH_TEST_INTEGRATION`,
  `TEST_INTEGRATION_ALL`, `TEST_BUILD_CHANGED_FUZZERS` and `TEST_BUILD_CHANGED_BENCHMARKS`
  (`python3 presubmit.py test_changed_integrations`, which isn't a default check).
- Fuzzer/benchmark Docker targets come from the generated `docker/generated.mk`
  (`docker/generate_makefile.py` + `docker/image_types.yaml`):
  - `make build-$FUZZER-$BENCHMARK`, `make build-$FUZZER-all`
  - `make run-$FUZZER-$BENCHMARK` (fuzz interactively), `make test-run-$FUZZER-$BENCHMARK` (short
    smoke run)
  - `make debug-builder-$FUZZER-$BENCHMARK` and `make debug-$FUZZER-$BENCHMARK` (shell into the
    builder/runner)
- Local experiment:
  `PYTHONPATH=. python3 experiment/run_experiment.py --experiment-config <cfg.yaml> --experiment-name <name> --fuzzers afl libfuzzer --benchmarks freetype2_ftfuzzer ...`
  (the config needs `local_experiment: true` and absolute `experiment_filestore`/`report_filestore`).
  Alternatively, use `make run-experiment` for the docker-compose setup (`compose/fuzzbench.yaml`).
- End-to-end test: `make run-end-to-end-test` (docker-compose with `compose/e2e-test.yaml`).
- Docs: `make docs-serve` (Jekyll, needs Ruby/bundler).

Style: yapf (`.style.yapf`) and pylint (`.pylintrc`). Every `.py`, `.sh`, `.c`, etc. file and every
`Dockerfile` needs the Apache 2.0 license header; `benchmarks/`, `database/alembic/` and any
`third_party/` directory are exempt.

## Architecture

### Integrations (most contributions land here)
- **`fuzzers/<name>/`**: `builder.Dockerfile` (builds the fuzzer on top of a benchmark's builder
  image), `runner.Dockerfile` (the runtime image), and `fuzzer.py`, which exposes `build()` (compiles
  the benchmark with the fuzzer's instrumentation, usually through `fuzzers/utils.py::build_benchmark`
  with CC/CXX/CFLAGS and `FUZZER_LIB` set) and `fuzz(input_corpus, output_corpus, target_binary)`,
  plus an optional `get_stats`. Variants usually import and reuse a parent fuzzer's `fuzzer.py` (e.g.
  `aflplusplus_*` reuse `aflplusplus`). `fuzzers/coverage/` is special: it's the clang source-based
  coverage build the measurer uses, not a real fuzzer. Fuzzer and benchmark names are validated by
  presubmit (`common/fuzzer_utils.py`, `common/benchmark_utils.py`).
- **`benchmarks/<name>/`**: an OSS-Fuzz-style `Dockerfile` (usually with a `build.sh`), `benchmark.yaml` (`project`,
  `fuzz_target`, `commit`, `commit_date`, optional `type: bug` / `unsupported_fuzzers`, etc.) and
  optional seeds. `benchmarks/oss_fuzz_benchmark_integration.py` imports new ones from OSS-Fuzz.

### Docker image graph
`docker/image_types.yaml` is the single source of truth for how images layer:
`base-image` → `{benchmark}-project-builder` (the benchmark's Dockerfile) →
`{fuzzer}-{benchmark}-builder-intermediate` (the fuzzer's `builder.Dockerfile`) →
`{fuzzer}-{benchmark}-builder` (`docker/benchmark-builder/Dockerfile`, which checks out the pinned
commit and runs `fuzzer_build` → `fuzzer.py::build()`) → runner images
(`docker/benchmark-runner/Dockerfile` on top of the fuzzer's `runner.Dockerfile`). The same YAML feeds
both `docker/generated.mk` for local builds and `experiment/build/generate_cloudbuild.py` for GCB, so
edit the YAML instead of either output.

### Experiment pipeline (`experiment/`)
1. `run_experiment.py` validates the config/fuzzers/benchmarks, then starts the **dispatcher**,
   either as a local Docker container or as a GCE instance (`common/gce.py`, `experiment/cloud`).
2. `dispatcher.py::dispatcher_main` builds all images (`experiment/build/builder.py`, choosing
   `local_build.py` or `gcb_build.py`), then runs three things concurrently:
   - `scheduler.py::schedule_loop`: starts and stops trial instances/containers and preempts, retries
     or finishes trials.
   - `measurer/measure_manager.py::measure_main`: pulls corpus snapshots from the filestore every
     cycle, runs them through the coverage build (`run_coverage.py`, `coverage_utils.py`) and crash
     reproduction (`run_crashes.py`), and writes `Snapshot`/`Crash` rows. Measure workers can run
     remotely, coordinated through a queue (`common/queue_utils.py`, `measure_worker.py`).
   - `reporter.py::output_report`: calls `analysis/generate_report.py` periodically.
3. `runner.py` runs inside each trial container. It calls the fuzzer's `fuzz()` and syncs corpus
   snapshots to the experiment filestore every cycle.

State lives in two places. The **database** (`database/models.py`, SQLAlchemy: `Experiment`, `Trial`,
`Snapshot`, `Crash`) is Postgres in production and SQLite in tests, with Alembic migrations in
`database/alembic/`; add a migration whenever you change a model. The **filestore**
(`common/filestore_utils.py`) sends paths either to `gsutil.py` (GCS) or to `local_filestore.py`,
depending on whether the experiment is local. Most runtime configuration comes from environment
variables (`EXPERIMENT`, `EXPERIMENT_FILESTORE`, `LOCAL_EXPERIMENT`, `CLOUD_PROJECT`, `DOCKER_REGISTRY`,
...), read through `common/experiment_utils.py` and `common/environment.py`.

### Other top-level pieces
- `analysis/`: report generation (`generate_report.py`, `experiment_results.py`, `stat_tests.py`,
  `plotting.py`, Jinja templates in `report_templates/`). You can run it standalone against a DB or
  CSV to make custom reports (`docs/developing-fuzzbench/custom_analysis_and_reports.md`).
- `service/`: the automated FuzzBench service. `experiment-requests.yaml` is the queue of requested
  experiments that `gcbrun_experiment.py` / `run_experiment_cloudbuild.yaml` launch; `core-fuzzers.yaml`
  lists the default fuzzers.
- `src_analysis/`: works out which fuzzers/benchmarks a diff touches (used by presubmit and by
  `.github/workflows/build_and_test_run_fuzzer_benchmarks.py` so CI only builds changed integrations).
- `common/`: shared utilities (subprocess wrapper `new_process.py`, logging, retry, config parsing,
  gcloud/gsutil wrappers).
