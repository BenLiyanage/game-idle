#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: DEV_ENGINE_RUNNER_DIR=<path> DEV_ENGINE_RUNNER_NAME=<name> $0 start|stop|status" >&2
}

command="${1:-}"
if [[ "$command" != "start" && "$command" != "stop" && "$command" != "status" ]]; then
  usage
  exit 64
fi

if [[ -z "${DEV_ENGINE_RUNNER_DIR:-}" || -z "${DEV_ENGINE_RUNNER_NAME:-}" ]]; then
  usage
  exit 64
fi

runner_dir="$(cd "$DEV_ENGINE_RUNNER_DIR" 2>/dev/null && pwd -P || true)"
if [[ -z "$runner_dir" || ! -d "$runner_dir" ]]; then
  echo "expected runner directory cannot be resolved: $DEV_ENGINE_RUNNER_DIR" >&2
  exit 40
fi

runner_file="$runner_dir/.runner"
svc="$runner_dir/svc.sh"
if [[ ! -f "$runner_file" || ! -x "$svc" ]]; then
  echo "expected GitHub runner identity or service control script is missing in $runner_dir" >&2
  exit 40
fi

actual_name="$(python3 - "$runner_file" <<'PY'
import json
import sys
from pathlib import Path

try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
except Exception:
    sys.exit(2)
print(payload.get("agentName", ""))
PY
)"
if [[ "$actual_name" != "$DEV_ENGINE_RUNNER_NAME" ]]; then
  echo "runner identity mismatch: expected '$DEV_ENGINE_RUNNER_NAME', found '${actual_name:-<empty>}'" >&2
  exit 40
fi

expected_repo="${DEV_ENGINE_RUNNER_REPO_URL:-https://github.com/BenLiyanage/game-idle}"
actual_repo="$(python3 - "$runner_file" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
print(payload.get("gitHubUrl") or payload.get("serverUrl") or "")
PY
)"
actual_repo="${actual_repo%/}"
expected_repo="${expected_repo%/}"
if [[ "$actual_repo" != "$expected_repo" ]]; then
  echo "runner repository mismatch: expected '$expected_repo', found '${actual_repo:-<empty>}'" >&2
  exit 40
fi

cd "$runner_dir"
case "$command" in
  start)
    exec "$svc" start
    ;;
  stop)
    exec "$svc" stop
    ;;
  status)
    service_status=0
    "$svc" status || service_status=$?
    if command -v gh >/dev/null 2>&1; then
      repo_slug="${expected_repo#https://github.com/}"
      gh api "repos/$repo_slug/actions/runners" \
        --jq ".runners[] | select(.name == \"$DEV_ENGINE_RUNNER_NAME\") | {name, status, busy, labels: [.labels[].name]}"
    else
      echo "GitHub runner availability was not checked because gh authentication is unavailable."
    fi
    exit "$service_status"
    ;;
esac
