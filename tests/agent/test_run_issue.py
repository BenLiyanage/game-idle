#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
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
        self.inputs: list[str | None] = []
        self.worktree_porcelain = ""
        self.branch_exists = False
        self.remote_branch_exists = False
        self.prs: list[dict[str, object]] = []
        self.codex_statuses: list[str] = ["success"]
        self.codex_parser_compatible = True
        self.validation_results: list[run_issue.CommandResult] = [
            run_issue.CommandResult(["bash", "tools/ci/verify.sh"], 0, "verification ok", "")
        ]
        self.status_output = " M README.md\n"

    def __call__(
        self, args: list[str], cwd: Path | None = None, input_text: str | None = None
    ) -> run_issue.CommandResult:
        self.calls.append(args)
        self.inputs.append(input_text)
        if args[:3] == ["git", "config", "--get"]:
            return run_issue.CommandResult(args, 0, "https://github.com/BenLiyanage/game-idle.git\n", "")
        if args[:3] == ["gh", "issue", "view"]:
            payload = {
                "number": int(args[3]),
                "title": "Selected issue",
                "body": "Body",
                "url": f"https://github.com/BenLiyanage/game-idle/issues/{args[3]}",
                "state": "OPEN",
            }
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
        if args[:2] == ["git", "branch"]:
            self.branch_exists = True
            return run_issue.CommandResult(args, 0, "", "")
        if args[:3] == ["git", "worktree", "add"]:
            return run_issue.CommandResult(args, 0, "", "")
        if args == [str(Path(sys.executable).resolve()), "--version"]:
            return run_issue.CommandResult(args, 0, "codex-test 1.0\n", "")
        if "exec" in args and args[-1] == "--help":
            if self.codex_parser_compatible:
                return run_issue.CommandResult(args, 0, "Run Codex non-interactively\n", "")
            return run_issue.CommandResult(args, 2, "", "error: unexpected argument '--ask-for-approval' found")
        if "exec" in args and "--output-last-message" in args:
            output_path = Path(args[args.index("--output-last-message") + 1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            status = self.codex_statuses.pop(0)
            output_path.write_text(
                json.dumps(
                    {
                        "status": status,
                        "summary": f"codex {status}",
                        "blockers": ["needs protected decision"] if status == "blocked" else [],
                    }
                ),
                encoding="utf-8",
            )
            return run_issue.CommandResult(args, 0, "codex done", "")
        if args[:2] == ["bash", "tools/ci/verify.sh"]:
            result = self.validation_results.pop(0)
            return run_issue.CommandResult(args, result.returncode, result.stdout, result.stderr)
        if args[:3] == ["git", "status", "--porcelain"]:
            return run_issue.CommandResult(args, 0, self.status_output, "")
        if args[:2] in (["git", "add"], ["git", "commit"], ["git", "push"]):
            return run_issue.CommandResult(args, 0, "", "")
        if args[:3] == ["gh", "pr", "edit"]:
            return run_issue.CommandResult(args, 0, "", "")
        if args[:3] == ["gh", "pr", "create"]:
            return run_issue.CommandResult(args, 0, "https://github.com/BenLiyanage/game-idle/pull/9\n", "")
        return run_issue.CommandResult(args, 1, "", f"unexpected command: {args}")


class RunIssueTests(unittest.TestCase):
    def worker_env(self, **values: str) -> dict[str, str]:
        return {"CODEX_AGENT_CODEX_BIN": str(Path(sys.executable).resolve()), **values}

    def test_invalid_issue_input_fails_clearly(self) -> None:
        with self.assertRaises(run_issue.WorkerError) as context:
            run_issue.validate_issue_number("selected-for-development")
        self.assertEqual(context.exception.exit_code, run_issue.EXIT_USAGE)

    def test_branch_and_worktree_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            layout = run_issue.build_layout(Path(tmp), 8, {})
        self.assertEqual(layout.branch, "agent/issue-8")
        self.assertTrue(str(layout.worktree).endswith(".worktrees/agent-issue-8"))

    def test_prompt_includes_required_constraints_from_template(self) -> None:
        issue = run_issue.Issue(8, "Title", "Body", "https://example.test/8", "OPEN")
        prompt = run_issue.prompt_for_issue(issue, Path("/tmp/wt"), "Contract")
        self.assertIn("Treat the selected GitHub issue above as the canonical implementation specification.", prompt)
        self.assertIn("Do not select, groom, prioritize, or implement any other issue.", prompt)
        self.assertIn("Return JSON matching the provided schema", prompt)
        self.assertIn("Never merge", prompt)
        self.assertIn("Contract", prompt)

    def test_dry_run_only_preflights_codex_and_does_not_mutate_github_or_run_docker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self.worker_env(CODEX_AGENT_RESULT_PATH=str(Path(tmp) / "result.json"))
            fake = FakeRunner()
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_issue.main(["8", "--dry-run"], command_runner=fake, env=env)
            result = json.loads((Path(tmp) / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        flattened = [" ".join(call[:3]) for call in fake.calls]
        self.assertFalse(any("--output-last-message" in call for call in fake.calls))
        self.assertNotIn("docker run --rm", flattened)
        self.assertNotIn("gh pr create", flattened)
        self.assertIn(" exec ", result["codex_command"])
        self.assertEqual(result["validation_command"], "bash tools/ci/verify.sh")

    def test_existing_pr_is_reused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result_dir = Path(tmp) / "agent"
            result_dir.mkdir()
            layout = run_issue.Layout(
                Path(tmp),
                Path(tmp) / ".worktrees",
                "agent/issue-8",
                Path(tmp),
                "main",
                result_dir,
                result_dir / "result.json",
            )
            fake = FakeRunner()
            fake.prs = [{"number": 12, "url": "https://github.com/BenLiyanage/game-idle/pull/12"}]
            url = run_issue.open_or_update_pr(
                run_issue.Issue(8, "Title", "Body", "url", "OPEN"), layout, "BenLiyanage/game-idle", "body", {}, fake
            )
        self.assertEqual(url, "https://github.com/BenLiyanage/game-idle/pull/12")
        self.assertTrue(any(call[:3] == ["gh", "pr", "edit"] for call in fake.calls))
        self.assertFalse(any(call[:3] == ["gh", "pr", "create"] for call in fake.calls))

    def test_validation_failure_gets_one_bounded_repair_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("Contract", encoding="utf-8")
            env = self.worker_env(
                CODEX_AGENT_WORKTREE_ROOT=str(root.parent),
                CODEX_AGENT_RESULT_DIR=str(root / ".codex-agent"),
                CODEX_AGENT_RESULT_PATH=str(root / ".codex-agent" / "result.json"),
            )
            fake = FakeRunner()
            fake.worktree_porcelain = f"worktree {root}\nHEAD abc\nbranch refs/heads/agent/issue-8\n"
            fake.codex_statuses = ["success", "success"]
            fake.validation_results = [
                run_issue.CommandResult(["bash", "tools/ci/verify.sh"], 1, "unit failure", ""),
                run_issue.CommandResult(["bash", "tools/ci/verify.sh"], 0, "verification ok", ""),
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_issue.main(["8"], command_runner=fake, env=env)
        self.assertEqual(code, run_issue.EXIT_SUCCESS)
        codex_calls = [call for call in fake.calls if "--output-last-message" in call]
        validation_calls = [call for call in fake.calls if call[:2] == ["bash", "tools/ci/verify.sh"]]
        self.assertEqual(len(codex_calls), 2)
        self.assertEqual(len(validation_calls), 2)
        self.assertIn("local validation command for issue #8 failed", "\n".join(text or "" for text in fake.inputs))

    def test_blocked_codex_result_has_distinct_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("Contract", encoding="utf-8")
            env = self.worker_env(
                CODEX_AGENT_WORKTREE_ROOT=str(root.parent),
                CODEX_AGENT_RESULT_DIR=str(root / ".codex-agent"),
                CODEX_AGENT_RESULT_PATH=str(root / ".codex-agent" / "result.json"),
            )
            fake = FakeRunner()
            fake.worktree_porcelain = f"worktree {root}\nHEAD abc\nbranch refs/heads/agent/issue-8\n"
            fake.codex_statuses = ["blocked"]
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_issue.main(["8"], command_runner=fake, env=env)
            result = json.loads((root / ".codex-agent" / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(code, run_issue.EXIT_BLOCKED)
        self.assertEqual(result["status"], "blocked")
        self.assertFalse(any(call[:2] == ["git", "commit"] for call in fake.calls))

    def test_missing_local_godot_is_recorded_without_repair_loop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("Contract", encoding="utf-8")
            env = self.worker_env(
                CODEX_AGENT_WORKTREE_ROOT=str(root.parent),
                CODEX_AGENT_RESULT_DIR=str(root / ".codex-agent"),
                CODEX_AGENT_RESULT_PATH=str(root / ".codex-agent" / "result.json"),
            )
            fake = FakeRunner()
            fake.worktree_porcelain = f"worktree {root}\nHEAD abc\nbranch refs/heads/agent/issue-8\n"
            fake.validation_results = [
                run_issue.CommandResult(
                    ["bash", "tools/ci/verify.sh"],
                    1,
                    "== structure ==\nstructure ok\n== godot ==\n",
                    "Godot is unavailable. Install Godot 4.7.2-stable or set GODOT_BIN.\n",
                )
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_issue.main(["8"], command_runner=fake, env=env)
            result = json.loads((root / ".codex-agent" / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(code, run_issue.EXIT_SUCCESS)
        self.assertEqual(result["validation_status"], "cloud_only_prerequisite_missing")
        self.assertEqual(len([call for call in fake.calls if "--output-last-message" in call]), 1)

    def test_validation_failure_after_retry_has_distinct_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "AGENTS.md").write_text("Contract", encoding="utf-8")
            env = self.worker_env(
                CODEX_AGENT_WORKTREE_ROOT=str(root.parent),
                CODEX_AGENT_RESULT_DIR=str(root / ".codex-agent"),
                CODEX_AGENT_RESULT_PATH=str(root / ".codex-agent" / "result.json"),
            )
            fake = FakeRunner()
            fake.worktree_porcelain = f"worktree {root}\nHEAD abc\nbranch refs/heads/agent/issue-8\n"
            fake.codex_statuses = ["success", "success"]
            fake.validation_results = [
                run_issue.CommandResult(["bash", "tools/ci/verify.sh"], 1, "unit failure", ""),
                run_issue.CommandResult(["bash", "tools/ci/verify.sh"], 1, "unit failure", ""),
            ]
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_issue.main(["8"], command_runner=fake, env=env)
        self.assertEqual(code, run_issue.EXIT_VALIDATION_FAILED)

    def test_worker_never_lists_or_selects_other_issues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = self.worker_env(CODEX_AGENT_RESULT_PATH=str(Path(tmp) / "result.json"))
            fake = FakeRunner()
            with contextlib.redirect_stdout(io.StringIO()):
                run_issue.main(["8", "--dry-run"], command_runner=fake, env=env)
        issue_calls = [call for call in fake.calls if call[:2] == ["gh", "issue"]]
        self.assertEqual(len(issue_calls), 1)
        self.assertEqual(issue_calls[0][2:4], ["view", "8"])

    def test_rejects_npm_container_codex_binary_hint(self) -> None:
        with self.assertRaises(run_issue.WorkerError):
            run_issue.codex_command(
                Path("/tmp/out.txt"), {"CODEX_AGENT_CODEX_BIN": "/workspace/node_modules/.bin/codex"}
            )

    def test_codex_command_uses_cli_0153_global_approval_contract(self) -> None:
        command = run_issue.codex_command(
            Path("/tmp/out.txt"),
            {
                "CODEX_AGENT_CODEX_BIN": "/Applications/ChatGPT.app/Contents/Resources/codex",
                "CODEX_AGENT_APPROVAL_POLICY": "never",
                "CODEX_AGENT_SANDBOX": "workspace-write",
            },
        )
        self.assertEqual(
            command[:6],
            [
                "/Applications/ChatGPT.app/Contents/Resources/codex",
                "--ask-for-approval",
                "never",
                "exec",
                "--sandbox",
                "workspace-write",
            ],
        )
        self.assertLess(command.index("--ask-for-approval"), command.index("exec"))

    def test_preflight_resolves_and_invokes_configured_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "result.json"
            fake = FakeRunner()
            env = self.worker_env(CODEX_AGENT_RESULT_PATH=str(result_path))
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_issue.main(["--preflight"], command_runner=fake, env=env)
            result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(code, run_issue.EXIT_SUCCESS)
        self.assertEqual(result["status"], "preflight_ok")
        self.assertEqual(result["codex_bin"], str(Path(sys.executable).resolve()))
        self.assertIn([str(Path(sys.executable).resolve()), "--version"], fake.calls)
        self.assertIn(
            [
                str(Path(sys.executable).resolve()),
                "--ask-for-approval",
                "never",
                "exec",
                "--sandbox",
                "workspace-write",
                "--help",
            ],
            fake.calls,
        )

    def test_incompatible_codex_parser_is_bounded_infrastructure_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "result.json"
            fake = FakeRunner()
            fake.codex_parser_compatible = False
            env = self.worker_env(CODEX_AGENT_RESULT_PATH=str(result_path))
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_issue.main(["--preflight"], command_runner=fake, env=env)
            result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(code, run_issue.EXIT_INFRASTRUCTURE_FAILED)
        self.assertEqual(result["status"], "infrastructure_failed")
        self.assertIn("exec argument contract", result["message"])
        self.assertIn("unexpected argument '--ask-for-approval'", result["message"])

    def test_missing_codex_is_bounded_infrastructure_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "result.json"
            env = {
                "CODEX_AGENT_CODEX_BIN": str(Path(tmp) / "missing-codex"),
                "CODEX_AGENT_RESULT_PATH": str(result_path),
            }
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_issue.main(["--preflight"], env=env)
            result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(code, run_issue.EXIT_INFRASTRUCTURE_FAILED)
        self.assertEqual(result["status"], "infrastructure_failed")
        self.assertIn("cannot be resolved or executed", result["message"])

    def test_empty_workflow_codex_configuration_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "result.json"
            env = {"CODEX_AGENT_CODEX_BIN": "", "CODEX_AGENT_RESULT_PATH": str(result_path)}
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_issue.main(["--preflight"], env=env)
            result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(code, run_issue.EXIT_INFRASTRUCTURE_FAILED)
        self.assertEqual(result["status"], "infrastructure_failed")
        self.assertIn("configured but empty", result["message"])

    def test_issue_run_fails_codex_preflight_before_issue_or_worktree_operations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "result.json"
            env = {
                "CODEX_AGENT_CODEX_BIN": str(Path(tmp) / "missing-codex"),
                "CODEX_AGENT_RESULT_PATH": str(result_path),
            }
            fake = FakeRunner()
            with contextlib.redirect_stdout(io.StringIO()):
                code = run_issue.main(["8"], command_runner=fake, env=env)
            result = json.loads(result_path.read_text(encoding="utf-8"))
        self.assertEqual(code, run_issue.EXIT_INFRASTRUCTURE_FAILED)
        self.assertEqual(result["status"], "infrastructure_failed")
        self.assertEqual(fake.calls, [])

    def test_missing_subprocess_is_returned_without_traceback(self) -> None:
        result = run_issue.run_command(["/definitely/missing/game-idle-codex"])
        self.assertEqual(result.returncode, 127)
        self.assertIn("No such file or directory", result.stderr)


if __name__ == "__main__":
    unittest.main()
