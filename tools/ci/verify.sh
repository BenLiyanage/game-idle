#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

EXPECTED_GODOT_VERSION="$(tr -d '[:space:]' < .godot-version)"
EXPECTED_GODOT_CLI_VERSION="${EXPECTED_GODOT_VERSION/-stable/.stable}"
EXPECTED_RUFF_VERSION="$(sed -n 's/^[[:space:]]*ruff==\([^[:space:]#]*\).*$/\1/p' requirements-ruff.txt)"
TOOL_ROOT="$(python3 tools/ci/bootstrap.py --print-tool-root)"
RUFF_BIN="$TOOL_ROOT/bin/ruff"

if [[ -z "$EXPECTED_RUFF_VERSION" ]]; then
  echo "requirements-ruff.txt must contain a ruff==<version> pin" >&2
  exit 1
fi

echo "== structure =="
bash tools/ci/check_repo_structure.sh

echo "== python quality =="
if [[ ! -x "$RUFF_BIN" ]]; then
  echo "Pinned Ruff is unavailable. Run: bash tools/ci/bootstrap.sh" >&2
  exit 1
fi
ruff_version="$("$RUFF_BIN" --version)"
echo "found: $ruff_version"
if [[ "$ruff_version" != "ruff $EXPECTED_RUFF_VERSION" ]]; then
  echo "Expected Ruff $EXPECTED_RUFF_VERSION. Run: bash tools/ci/bootstrap.sh" >&2
  exit 1
fi
"$RUFF_BIN" check .
"$RUFF_BIN" format --check .
python3 -m unittest discover

echo "== godot =="
if [[ -n "${GODOT_BIN:-}" ]]; then
  if [[ ! -x "$GODOT_BIN" ]]; then
    echo "GODOT_BIN is set but not executable: $GODOT_BIN" >&2
    exit 1
  fi
else
  GODOT_BIN="$TOOL_ROOT/bin/godot"
  if [[ ! -x "$GODOT_BIN" ]]; then
    echo "Pinned Godot is unavailable. Run: bash tools/ci/bootstrap.sh" >&2
    exit 1
  fi
fi

actual_version="$("$GODOT_BIN" --version)"
echo "found: $actual_version"
if [[ "$actual_version" != "$EXPECTED_GODOT_CLI_VERSION"* && "$actual_version" != "$EXPECTED_GODOT_VERSION"* ]]; then
  echo "Expected Godot $EXPECTED_GODOT_VERSION, got: $actual_version" >&2
  exit 1
fi

echo "== headless import =="
"$GODOT_BIN" --headless --quit --path .

echo "== scene smoke test =="
"$GODOT_BIN" --headless --path . --script res://tests/headless/test_main_scene.gd

echo "verification ok"
