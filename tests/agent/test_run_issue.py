#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "agent" / "run_issue.py"
SPEC = importlib.util.spec_from_file_location("run_issue", MODULE_PATH)
run_issue = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["run_issue"] = run_issue
SPEC.loader.exec_module(run_issue)


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.worktree_porcelain = ""
        self.branch_exists = False
        self.remote_branch_exists = False
        self.prs: list[dict[str, object]] = []
        self.codex_status = "success"
        self.verify_returncode = 0
        self.status_output = " M README.md\n"
        self.base_sha = "a" * 40

    def __call__(self, args: list[str], cwd: Path | None = None, input_text: str | None = None) -> run_issue.CommandResult:
        self.calls.append(args)
        if args[:3] == ["git", "config", "--get"]:
            return run_issue.CommandResult(args, 0, "https://github.com/BenLiyanage/game-idle.git\n", "")
        if args[:3] == ["gh", "issue", "view"]:
            payload = {"number": 8, "title": "Add a repository-owned local Codex issue worker", "body": "Body", "url": "https://github.com/BenLiyanage/game-idle/issues/8", "state": "OPEN"}
            return run_issue.CommandResult(args, 0, json.dumps(payload), "")
        if args[:3] == ["git", "worktree", "list"]:
            return run_issue.CommandResult(args, 0, self.worktree_porcelain, "")
        if args[:4] == ["git", "show-ref", "--verify", "--quiet"]:
            ref = args[4]
            exists = self.remote_branch_exists if ref.startswith("refs/remotes/") else self.branch_exists
            return run_issue.CommandResult(args, 0 if exists else 1, "", "")
        if args[:3] == ["gh", "pr", "list"]:
            return run_issue.CommandResult(args, 0, json.dumps(self.prs), "")
        if args[:3] == ["git", "fetch", "origin"]:
            return run_issue.CommandResult(args, 0, "", "")
        if args[:2] == ["git", "rev-parse"]:
            return run_issue.CommandResult(args, 0, f"{self.base_sha}\n", "")
        if args[:2] == ["git", "branch"]:
            self.branch_exists = True
            return run_issue.CommandResult(args, 0, "", "")
        if args[:3] == ["git", "worktree", "add"]:
            return run_issue.CommandResult(args, 0, "", "")
        if args[:2] == ["docker", "run"]:
            result_mount = next(part for index, part in enumerate(args) if index > 0 and args[index - 1] == "--mount" and "dst=/results" in part)
            result_dir = Path(result_mount.split("src=", 1)[1].split(",dst=", 1)[0])
            output_path = result_dir / "codex-result.json"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps({"status": self.codex_status, "summary": "done", "blockers": ["needs decision"] if self.codex_status == "blocked" else []}), encoding="utf-8")
            return run_issue.CommandResult(args, self.verify_returncode, "worker", "verification failed" if self.verify_returncode else "")
        if args[:3] == ["git", "status", "--porcelain"]:
            return run_issue.CommandResult(args, 0, self.status_output, "")
        if args[:3] == ["git", "diff", "--name-only"]:
            return run_issue.CommandResult(args, 0, "README.md\n", "")
        if args[:3] == ["git", "diff", "--binary"]:
            return run_issue.CommandResult(args, 0, "diff --git a/README.md b/README.md\n--- a/README.md\n+++ b/README.md\n@@ -1 +1 @@\n-old\n+new\n", "")
        if args[:2] in (["git", "add"], ["git", "commit"], ["git", "push"]):
            return run_issue.CommandResult(args, 0, "", "")
        if args[:3] == ["gh", "pr", "edit"]:
            return run_issue.CommandResult(args, 0, "", "")
        if args[:3] == ["gh", "pr", "create"]:
            return run_issue.CommandResult(args, 0, "https://github.com/BenLiyanage/game-idle/pull/9\n", "")
        return run_issue.CommandResult(args, 1, "", f"unexpected command: {args}")


class RunIssueTests(unittest.TestCase):
    def test_invalid_issue_input_fails_clearly(self) -> None:
        with self.assertRaises(run_issue.WorkerError) as context:
            run_issue.validate_issue_number("selected-for-development")
        self.assertEqual(context.exception.exit_code, run_issue.EXIT_USAGE)

    def test_branch_and_worktree_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = run_issue.build_layout(Path(tmp), 8, {})
        self.assertEqual(layout.branch, "agent/issue-8")
        self.assertTrue(str(layout.worktree).endswith(".worktrees/agent-issue-8"))

    def test_prompt_includes_required_constraints(self) -> None:
        issue = run_issue.Issue(8, "Title", "Body", "https://example.test/8", "OPEN")
        prompt = run_issue.prompt_for_issue(issue, Path("/tmp/wt"), "Contract")
        self.assertIn("Treat the selected GitHub issue above as the canonical implementation specification.", prompt)
        self.assertIn("Do not select, groom, prioritize, or implement any other issue.", prompt)
        self.assertIn("Run bash tools/ci/verify.sh", prompt)
        self.assertIn("Never merge", prompt)
        self.assertIn("Contract", prompt)

    def test_dry_run_does_not_invoke_codex_or_mutate_github(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"CODEX_AGENT_RESULT_PATH": str(Path(tmp) / "result.json")}
            fake = FakeRunner()
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_issue.main(["8", "--dry-run"], command_runner=fake, env=env)
            result = json.loads((Path(tmp) / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        flattened = [" ".join(call[:3]) for call in fake.calls]
        self.assertNotIn("codex exec --cd", flattened)
        self.assertNotIn("gh pr create", flattened)
        self.assertNotIn("git push -u", flattened)
        self.assertIn("--network none", result["docker_command"])
        self.assertIn("--read-only", result["docker_command"])
        self.assertIn("--cap-drop ALL", result["docker_command"])

    def test_worker_environment_has_no_github_write_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            layout = run_issue.Layout(
                repo_root=root,
                worktree_root=root / ".worktrees",
                branch="agent/issue-36",
                worktree=root / ".worktrees" / "agent-issue-36",
                base_branch="main",
                result_dir=root / ".codex-agent" / "issue-36",
                result_path=root / ".codex-agent" / "issue-36" / "result.json",
            )
            layout.worktree.mkdir(parents=True)
            layout.result_dir.mkdir(parents=True)
            env = {"GITHUB_TOKEN": "write-token-that-must-not-cross", "GH_TOKEN": "write-token-that-must-not-cross"}
            command = run_issue.docker_run_command(
                run_issue.IsolationConfig("image", "2", "4g", "256", "none", False, 1000, 1000, True, True, "ALL", "no-new-privileges:true", ()),
                layout,
                "container",
                None,
                ["python3", "/results/worker_payload.py"],
                env,
            )
        rendered = run_issue.command_line(command)
        self.assertNotIn("GITHUB_TOKEN", rendered)
        self.assertNotIn("GH_TOKEN", rendered)
        self.assertNotIn(".config/gh", rendered)
        self.assertNotIn(".ssh", rendered)
        self.assertNotIn(".git-credentials", rendered)

    def test_worker_success_only_writes_artifact_without_github_mutation_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("Contract", encoding="utf-8")
            env = {
                "CODEX_AGENT_WORKTREE_ROOT": str(root.parent),
                "CODEX_AGENT_RESULT_DIR": str(root / ".codex-agent"),
                "CODEX_AGENT_RESULT_PATH": str(root / ".codex-agent" / "result.json"),
                "CODEX_AGENT_SKIP_CODEX_AUTH": "1",
            }
            fake = FakeRunner()
            fake.worktree_porcelain = f"worktree {root}\nHEAD abc\nbranch refs/heads/agent/issue-8\n"
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_issue.main(["8"], command_runner=fake, env=env)
            result = json.loads((root / ".codex-agent" / "result.json").read_text(encoding="utf-8"))
            artifact_exists = (root / ".codex-agent" / "change-artifact.json").exists()
        self.assertEqual(code, 0)
        self.assertEqual(result["publisher_ran"], False)
        self.assertTrue(artifact_exists)
        self.assertFalse(any(call[:2] == ["git", "push"] for call in fake.calls))
        self.assertFalse(any(call[:3] == ["gh", "pr", "create"] for call in fake.calls))

    def test_existing_pr_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result_dir = Path(tmp) / "agent"
            result_dir.mkdir()
            layout = run_issue.Layout(Path(tmp), Path(tmp) / ".worktrees", "agent/issue-8", Path(tmp), "main", result_dir, result_dir / "result.json")
            fake = FakeRunner()
            fake.prs = [{"number": 12, "url": "https://github.com/BenLiyanage/game-idle/pull/12"}]
            url = run_issue.open_or_update_pr(run_issue.Issue(8, "Title", "Body", "url", "OPEN"), layout, "BenLiyanage/game-idle", "body", {}, fake)
        self.assertEqual(url, "https://github.com/BenLiyanage/game-idle/pull/12")
        self.assertTrue(any(call[:3] == ["gh", "pr", "edit"] for call in fake.calls))
        self.assertFalse(any(call[:3] == ["gh", "pr", "create"] for call in fake.calls))

    def test_blocked_result_has_distinct_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("Contract", encoding="utf-8")
            env = {
                "CODEX_AGENT_WORKTREE_ROOT": str(root.parent),
                "CODEX_AGENT_RESULT_DIR": str(root / ".codex-agent"),
                "CODEX_AGENT_RESULT_PATH": str(root / ".codex-agent" / "result.json"),
                "CODEX_AGENT_SKIP_CODEX_AUTH": "1",
            }
            fake = FakeRunner()
            fake.worktree_porcelain = f"worktree {root}\nHEAD abc\nbranch refs/heads/agent/issue-8\n"
            fake.codex_status = "blocked"
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_issue.main(["8"], command_runner=fake, env=env)
        self.assertEqual(code, run_issue.EXIT_BLOCKED)

    def test_verification_failure_is_distinguishable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("Contract", encoding="utf-8")
            env = {
                "CODEX_AGENT_WORKTREE_ROOT": str(root.parent),
                "CODEX_AGENT_RESULT_DIR": str(root / ".codex-agent"),
                "CODEX_AGENT_RESULT_PATH": str(root / ".codex-agent" / "result.json"),
                "CODEX_AGENT_SKIP_CODEX_AUTH": "1",
            }
            fake = FakeRunner()
            fake.worktree_porcelain = f"worktree {root}\nHEAD abc\nbranch refs/heads/agent/issue-8\n"
            fake.verify_returncode = 1
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_issue.main(["8"], command_runner=fake, env=env)
        self.assertEqual(code, run_issue.EXIT_VERIFICATION_FAILED)

    def test_worker_never_lists_or_selects_other_issues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {"CODEX_AGENT_RESULT_PATH": str(Path(tmp) / "result.json")}
            fake = FakeRunner()
            with contextlib.redirect_stdout(io.StringIO()):
                run_issue.main(["8", "--dry-run"], command_runner=fake, env=env)
        issue_calls = [call for call in fake.calls if call[:2] == ["gh", "issue"]]
        self.assertEqual(len(issue_calls), 1)
        self.assertEqual(issue_calls[0][2:4], ["view", "8"])

    def test_isolation_config_fails_closed_on_privileged_drift(self) -> None:
        with self.assertRaises(run_issue.WorkerError) as context:
            run_issue.validate_isolation_config(
                run_issue.IsolationConfig(
                    image="image",
                    cpus="2",
                    memory="4g",
                    pids_limit="256",
                    network="none",
                    allow_bridge_network_when_explicit=False,
                    uid=0,
                    gid=0,
                    readonly_rootfs=False,
                    no_new_privileges=False,
                    cap_drop="NET_ADMIN",
                    security_opt="seccomp=unconfined",
                    tmpfs=(),
                ),
                "none",
            )
        self.assertIn("non-root", context.exception.message)
        self.assertIn("read-only root filesystem", context.exception.message)
        self.assertIn("all Linux capabilities", context.exception.message)

    def test_bridge_network_requires_explicit_acknowledgement(self) -> None:
        env = {"CODEX_AGENT_WORKER_NETWORK": "bridge"}
        with self.assertRaises(run_issue.WorkerError):
            run_issue.load_isolation_config(Path(__file__).resolve().parents[2], env)
        allowed = run_issue.load_isolation_config(
            Path(__file__).resolve().parents[2],
            {"CODEX_AGENT_WORKER_NETWORK": "bridge", "CODEX_AGENT_ALLOW_WORKER_NETWORK": "1"},
        )
        self.assertEqual(allowed.network, "bridge")


if __name__ == "__main__":
    unittest.main()
