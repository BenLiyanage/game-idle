#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "agent" / "run_issue.py"
SPEC = importlib.util.spec_from_file_location("run_issue", MODULE_PATH)
run_issue = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["run_issue"] = run_issue
SPEC.loader.exec_module(run_issue)


def main() -> int:
    if shutil.which("docker") is None:
        print("SKIP: docker is unavailable; security probe requires a local Docker daemon", file=sys.stderr)
        return 77

    repo_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="codex-agent-security-") as tmp_raw:
        tmp = Path(tmp_raw)
        worktree_root = tmp / "worktrees"
        worktree = worktree_root / "agent-issue-35"
        result_dir = tmp / "results"
        protected = tmp / "protected"
        other_repo = tmp / "other-repo"
        fake_home = protected / "home"
        for path in (worktree, result_dir, other_repo, fake_home / ".ssh", fake_home / ".config" / "gh"):
            path.mkdir(parents=True, exist_ok=True)
        (protected / "host-secret.txt").write_text("HOST_SECRET_SHOULD_NOT_BE_READ\n", encoding="utf-8")
        (fake_home / ".ssh" / "id_ed25519").write_text("SSH_SECRET_SHOULD_NOT_BE_READ\n", encoding="utf-8")
        (fake_home / ".git-credentials").write_text("GIT_CREDENTIAL_SHOULD_NOT_BE_READ\n", encoding="utf-8")
        (fake_home / ".config" / "gh" / "hosts.yml").write_text("GH_SECRET_SHOULD_NOT_BE_READ\n", encoding="utf-8")
        (other_repo / "README.md").write_text("do not mutate\n", encoding="utf-8")

        probe = worktree / "probe.sh"
        probe.write_text(
            f"""#!/bin/sh
set -eu
failures=""
check_fail() {{
  name="$1"
  shift
  if "$@"; then
    failures="${{failures}} ${{name}}"
  fi
}}

check_fail read_host_secret cat {protected / "host-secret.txt"}
check_fail read_ssh cat {fake_home / ".ssh" / "id_ed25519"}
check_fail read_git_credentials cat {fake_home / ".git-credentials"}
check_fail read_gh_credentials cat {fake_home / ".config" / "gh" / "hosts.yml"}
check_fail mutate_other_repo sh -c 'echo pwned > {other_repo / "pwned.txt"}'
check_fail read_docker_socket test -S /var/run/docker.sock
if command -v docker >/dev/null 2>&1; then
  check_fail docker_daemon docker ps
fi
check_fail write_etc sh -c 'echo pwned > /etc/codex-agent-probe'

if grep -q '^NoNewPrivs:[[:space:]]*1$' /proc/self/status; then
  :
else
  failures="${{failures}} no_new_privileges"
fi
if grep -q '^CapEff:[[:space:]]*0000000000000000$' /proc/self/status; then
  :
else
  failures="${{failures}} capabilities_not_dropped"
fi
if awk '$2 == "00000000" {{ found=1 }} END {{ exit found ? 0 : 1 }}' /proc/net/route; then
  failures="${{failures}} network_default_route"
fi

echo workspace-ok > /workspace/probe-workspace-ok.txt
echo result-ok > /results/probe-result-artifact.txt
printf '%s\\n' "$failures" > /results/probe-failures.txt
test -z "$failures"
""",
            encoding="utf-8",
        )

        layout = run_issue.Layout(
            repo_root=repo_root,
            worktree_root=worktree_root,
            branch="agent/issue-35",
            worktree=worktree,
            base_branch="main",
            result_dir=result_dir,
            result_path=result_dir / "result.json",
        )
        env = {"CODEX_AGENT_SKIP_CODEX_AUTH": "1"}
        if os.environ.get("CODEX_AGENT_SECURITY_IMAGE"):
            env["CODEX_AGENT_WORKER_IMAGE"] = os.environ["CODEX_AGENT_SECURITY_IMAGE"]
        config = run_issue.load_isolation_config(repo_root, env)
        command = run_issue.docker_run_command(config, layout, "codex-security-probe", None, ["sh", "/workspace/probe.sh"], env)
        completed = subprocess.run(command, cwd=repo_root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        failures_path = result_dir / "probe-failures.txt"
        failures = failures_path.read_text(encoding="utf-8").strip() if failures_path.exists() else "<missing failures file>"
        report = {
            "command": run_issue.command_line(command),
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "failures": failures,
            "workspace_write_survived": (worktree / "probe-workspace-ok.txt").exists(),
            "result_artifact_survived": (result_dir / "probe-result-artifact.txt").exists(),
            "other_repo_mutated": (other_repo / "pwned.txt").exists(),
        }
        print(json.dumps(report, indent=2, sort_keys=True))
        if completed.returncode != 0:
            return 1
        if failures or not report["workspace_write_survived"] or not report["result_artifact_survived"] or report["other_repo_mutated"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
