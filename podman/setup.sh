#!/usr/bin/env bash
# Install host dependencies for a prepared checkout, without sudo.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ ! -f podman/adaptation.patch ]]; then
  echo 'Run prepare.py first, then run setup.sh in the prepared checkout.' >&2
  exit 1
fi

export PATH="$HOME/.local/bin:$PATH"
if ! command -v docker >/dev/null || ! docker --version 2>/dev/null | grep -q '^Docker version'; then
  case "$(uname -m)" in
    x86_64) docker_arch=x86_64 ;;
    aarch64|arm64) docker_arch=aarch64 ;;
    *) echo 'Unsupported architecture for the Docker client download.' >&2; exit 1 ;;
  esac
  command -v curl >/dev/null
  docker_tmp=$(mktemp -d)
  trap 'rm -rf -- "$docker_tmp"' EXIT
  curl --fail --location --retry 3 \
    "https://download.docker.com/linux/static/stable/$docker_arch/docker-20.10.24.tgz" \
    --output "$docker_tmp/docker.tgz"
  tar -xzf "$docker_tmp/docker.tgz" -C "$docker_tmp" docker/docker
  mkdir -p "$HOME/.local/bin"
  install -m 755 "$docker_tmp/docker/docker" "$HOME/.local/bin/docker"
fi

if [[ ! -x .venv/bin/python ]]; then
  setup_python=${PYTHON:-}
  if [[ -z "$setup_python" ]]; then
    for candidate in python3.10 python3.9 python3; do
      if command -v "$candidate" >/dev/null; then
        setup_python=$candidate
        break
      fi
    done
  fi
  "${setup_python:-python3}" -c \
    'import sys; assert (3, 9) <= sys.version_info[:2] <= (3, 10), "Use Python 3.9 or 3.10 (PYTHON=python3.10 bash podman/setup.sh)"'
  "$setup_python" -m venv .venv
fi
.venv/bin/python -c \
  'import sys; assert (3, 9) <= sys.version_info[:2] <= (3, 10), "Existing .venv needs Python 3.9 or 3.10"'
.venv/bin/python -m pip install --upgrade pip 'setuptools==75.8.0'
.venv/bin/python -m pip install -r requirements.txt 'setuptools==75.8.0'
.venv/bin/python -c 'import pkg_resources; import google.cloud.logging; print("Imports OK")'
echo 'Setup complete. Next: source .venv/bin/activate'
echo 'Then: python podman/run.py check && python podman/run.py build'
