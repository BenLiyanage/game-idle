#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

EXPECTED_GODOT_VERSION="$(tr -d '[:space:]' < .godot-version)"

echo "== structure =="
bash tools/ci/check_repo_structure.sh

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
