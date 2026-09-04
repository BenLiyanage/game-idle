#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

EXPECTED_GODOT_VERSION="$(tr -d '[:space:]' < .godot-version)"

echo "== structure =="
bash tools/ci/check_repo_structure.sh

echo "== python quality =="
if ! python3 -m ruff --version >/tmp/game-idle-ruff-version.txt 2>/tmp/game-idle-ruff-version.err; then
  echo "Ruff is unavailable. Install the pinned repository tool with: python3 -m pip install -r requirements-ruff.txt" >&2
  cat /tmp/game-idle-ruff-version.err >&2
  exit 1
fi
ruff_version="$(cat /tmp/game-idle-ruff-version.txt)"
echo "found: $ruff_version"
if [[ "$ruff_version" != "ruff 0.16.6" ]]; then
  echo "Expected Ruff 0.16.6. Install the pinned repository tool with: python3 -m pip install -r requirements-ruff.txt" >&2
  exit 1
fi
python3 -m ruff check .
python3 -m ruff format --check .

echo "== godot =="
if [[ -n "${GODOT_BIN:-}" ]]; then
  if [[ ! -x "$GODOT_BIN" ]]; then
    echo "GODOT_BIN is set but not executable: $GODOT_BIN" >&2
    exit 1
  fi
else
  if ! GODOT_BIN="$(command -v godot)"; then
    echo "Godot is unavailable. Install Godot $EXPECTED_GODOT_VERSION or set GODOT_BIN." >&2
    exit 1
  fi
fi

actual_version="$("$GODOT_BIN" --version)"
echo "found: $actual_version"
if [[ "$actual_version" != 4.7.2.stable* && "$actual_version" != 4.7.2-stable* ]]; then
  echo "Expected Godot $EXPECTED_GODOT_VERSION, got: $actual_version" >&2
  exit 1
fi

echo "== headless import =="
"$GODOT_BIN" --headless --quit --path .

echo "== scene smoke test =="
"$GODOT_BIN" --headless --path . --script res://tests/headless/test_main_scene.gd

echo "verification ok"
