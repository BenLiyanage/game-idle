#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "== structure =="
bash tools/ci/check_repo_structure.sh

echo "== python unit tests =="
python3 -m unittest discover -s tests -p "test_*.py"

if [[ -f pyproject.toml || -f ruff.toml || -f .ruff.toml ]] && python3 -m ruff --version >/tmp/game-idle-local-ruff-version.txt 2>/tmp/game-idle-local-ruff-version.err; then
  echo "== python ruff =="
  cat /tmp/game-idle-local-ruff-version.txt
  python3 -m ruff check .
  python3 -m ruff format --check .
else
  echo "== python ruff =="
  echo "Ruff is unavailable or not configured in this checkout; GitHub-hosted CI remains authoritative where configured."
fi

if [[ -n "${GODOT_BIN:-}" ]] || command -v godot >/dev/null 2>&1; then
  echo "== full verify =="
  bash tools/ci/verify.sh
else
  echo "== full verify =="
  echo "Godot unavailable locally; not duplicating GitHub-hosted pinned Godot provisioning on this host."
fi

echo "local validation ok"
