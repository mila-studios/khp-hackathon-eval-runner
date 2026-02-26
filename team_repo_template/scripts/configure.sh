#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

VENV_DIR="${VENV_DIR:-.venv}"

echo "== configure =="
echo "Mode: ${HACKATHON_MODE:-unknown} (gpu=${HACKATHON_NEEDS_GPU:-0})"

# Goal of this step:
# - Install dependencies
# - Download / materialize any model files you need (optional)

if [[ -f requirements.txt ]]; then
  echo "Installing Python dependencies from requirements.txt"
  if [[ ! -d "$VENV_DIR" ]]; then
    python3 -m venv "$VENV_DIR"
  fi
  PY="$VENV_DIR/bin/python"
  "$PY" -m pip install -U pip >/dev/null
  "$PY" -m pip install -r requirements.txt
else
  echo "No requirements.txt found. Skipping dependency install."
fi

echo "OK"

