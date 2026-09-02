#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

required_paths=(
  ".github/ISSUE_TEMPLATE/bug.md"
  ".github/ISSUE_TEMPLATE/epic.md"
  ".github/ISSUE_TEMPLATE/feature.md"
  ".github/pull_request_template.md"
  ".github/workflows/ci.yml"
  ".godot-version"
  "AGENTS.md"
  "LICENSE"
  "README.md"
  "assets/ui"
  "docs/architecture.md"
  "docs/development-loop.md"
  "export_presets.cfg"
  "project.godot"
  "scenes/main.tscn"
  "scripts/main.gd"
  "tests/headless/test_main_scene.gd"
  "tools/ci/check_repo_structure.sh"
  "tools/ci/verify.sh"
)

for path in "${required_paths[@]}"; do
  if [[ ! -e "$path" ]]; then
    echo "missing required path: $path" >&2
    exit 1
  fi
done

if ! grep -Eq '^run/main_scene="res://[^"]+\.tscn"$' project.godot; then
  echo "project.godot must declare run/main_scene as a .tscn resource" >&2
  exit 1
fi

main_scene="$(sed -n 's/^run\/main_scene="res:\/\/\(.*\.tscn\)"$/\1/p' project.godot | head -n 1)"
if [[ -z "$main_scene" || ! -f "$main_scene" ]]; then
  echo "project.godot main scene does not exist: ${main_scene:-<unset>}" >&2
  exit 1
fi

echo "structure ok"
