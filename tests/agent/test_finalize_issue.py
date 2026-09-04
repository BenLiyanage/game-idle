#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "agent" / "finalize_issue.py"
SPEC = importlib.util.spec_from_file_location("finalize_issue", MODULE_PATH)
finalize_issue = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["finalize_issue"] = finalize_issue
SPEC.loader.exec_module(finalize_issue)


BASE_SHA = "a" * 40
BRANCH = "agent/issue-36"
REPO = "BenLiyanage/game-idle"


def artifact(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "repository": REPO,
        "issue": {"number": 36, "title": "Separate untrusted Codex execution", "url": "https://github.com/BenLiyanage/game-idle/issues/36"},
        "base": {"branch": "main", "sha": BASE_SHA},
        "branch": BRANCH,
        "run": {"id": "issue-36-test"},
        "status": "success",
        "change": {
            "format": "git-diff-binary",
            "changed_paths": ["docs/agent/trusted-finalizer.md"],
            "patch": "diff --git a/docs/agent/trusted-finalizer.md b/docs/agent/trusted-finalizer.md\nnew file mode 100644\nindex 0000000..1111111\n--- /dev/null\n+++ b/docs/agent/trusted-finalizer.md\n@@ -0,0 +1 @@\n+trusted finalizer\n",
        },
        "verification": {
            "trusted_finalizer_reran": False,
            "command": "bash tools/ci/verify.sh",
            "environment": "disposable untrusted worker",
            "worker_exit_code": 0,
        },
        "provenance": {"worker_contract": "tools/agent/run_issue.sh"},
        "publisher": {"ran": False, "required": False},
    }
    payload.update(overrides)
    return payload


class FakeFinalizerRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.inputs: list[str | None] = []
        self.origin_url = "https://github.com/BenLiyanage/game-idle.git\n"
        self.origin_base = BASE_SHA
        self.branch_commit: str | None = None
        self.prs: list[dict[str, object]] = []
        self.last_commit_message = ""
        self.last_artifact_hash = ""
        self.git_diff_paths = "docs/agent/trusted-finalizer.md\n"
        self.created_pr_url = "https://github.com/BenLiyanage/game-idle/pull/99\n"
        self.executed_worker_content = False

    def __call__(
        self,
        args: list[str],
        cwd: Path | None = None,
        input_text: str | None = None,
        env: dict[str, str] | None = None,
    ) -> finalize_issue.CommandResult:
        self.calls.append(args)
        self.inputs.append(input_text)
        if args[:3] == ["git", "config", "--get"]:
            return finalize_issue.CommandResult(args, 0, self.origin_url, "")
        if args[:3] == ["git", "fetch", "origin"]:
            return finalize_issue.CommandResult(args, 0, "", "")
        if args[:2] == ["git", "rev-parse"]:
            return finalize_issue.CommandResult(args, 0, f"{self.origin_base}\n", "")
        if args[:4] == ["git", "show-ref", "--verify", "--hash"]:
            if self.branch_commit:
                return finalize_issue.CommandResult(args, 0, f"{self.branch_commit}\n", "")
            return finalize_issue.CommandResult(args, 1, "", "missing")
        if args[:3] == ["git", "log", "-1"]:
            if self.branch_commit and self.last_artifact_hash:
                return finalize_issue.CommandResult(args, 0, f"Artifact-Hash: {self.last_artifact_hash}\n", "")
            return finalize_issue.CommandResult(args, 1, "", "missing")
        if args[:2] == ["git", "read-tree"]:
            self._assert_safe_git_env(env)
            return finalize_issue.CommandResult(args, 0, "", "")
        if args[:3] == ["git", "apply", "--cached"]:
            self._assert_safe_git_env(env)
            if input_text and "sh -c" in input_text:
                self.executed_worker_content = False
            return finalize_issue.CommandResult(args, 0, "", "")
        if args[:4] == ["git", "diff", "--cached", "--name-only"]:
            return finalize_issue.CommandResult(args, 0, self.git_diff_paths, "")
        if args[:2] == ["git", "write-tree"]:
            return finalize_issue.CommandResult(args, 0, "tree-sha\n", "")
        if args[:2] == ["git", "commit-tree"]:
            self.last_commit_message = input_text or ""
            self.last_artifact_hash = self.last_commit_message.split("Artifact-Hash: ", 1)[1].splitlines()[0]
            return finalize_issue.CommandResult(args, 0, "commit-sha\n", "")
        if args[:2] == ["git", "update-ref"]:
            self.branch_commit = args[3]
            return finalize_issue.CommandResult(args, 0, "", "")
        if args[:3] == ["git", "push", "origin"]:
            return finalize_issue.CommandResult(args, 0, "", "")
        if args[:3] == ["gh", "pr", "list"]:
            return finalize_issue.CommandResult(args, 0, json.dumps(self.prs), "")
        if args[:3] == ["gh", "pr", "create"]:
            self.prs = [{"number": 99, "url": self.created_pr_url.strip()}]
            return finalize_issue.CommandResult(args, 0, self.created_pr_url, "")
        if args[:3] == ["gh", "pr", "edit"]:
            return finalize_issue.CommandResult(args, 0, "", "")
        return finalize_issue.CommandResult(args, 1, "", f"unexpected command: {args}")

    def _assert_safe_git_env(self, env: dict[str, str] | None) -> None:
        assert env is not None
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert env["GIT_CONFIG_SYSTEM"] == "/dev/null"


class FinalizeIssueTests(unittest.TestCase):
    def expected(self) -> finalize_issue.Expected:
        return finalize_issue.Expected(REPO, 36, "main", BASE_SHA, BRANCH)

    def write_artifact(self, payload: dict[str, Any], root: Path) -> Path:
        path = root / "artifact.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def run_finalize(self, payload: dict[str, Any], runner: FakeFinalizerRunner | None = None) -> tuple[dict[str, Any], FakeFinalizerRunner]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.write_artifact(payload, root)
            fake = runner or FakeFinalizerRunner()
            result = finalize_issue.finalize(path, self.expected(), root, {}, fake)
        return result, fake

    def assertFailsBeforeMutation(self, payload: dict[str, Any], message: str) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.write_artifact(payload, root)
            fake = FakeFinalizerRunner()
            with self.assertRaises(finalize_issue.FinalizerError) as context:
                finalize_issue.finalize(path, self.expected(), root, {}, fake)
        self.assertIn(message, context.exception.message)
        mutating = [call for call in fake.calls if call[:2] in (["git", "update-ref"], ["git", "push"]) or call[:3] == ["gh", "pr", "create"]]
        self.assertEqual(mutating, [])

    def test_valid_artifact_finalizes_successfully(self) -> None:
        result, fake = self.run_finalize(artifact())
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["repository"], REPO)
        self.assertEqual(result["issue"], 36)
        self.assertEqual(result["branch"], BRANCH)
        self.assertEqual(result["commit"], "commit-sha")
        self.assertIn("Artifact-Hash: ", fake.last_commit_message)
        self.assertTrue(any(call[:2] == ["git", "commit-tree"] for call in fake.calls))
        self.assertTrue(any(call[:3] == ["gh", "pr", "create"] for call in fake.calls))

    def test_malformed_artifact_fails_closed(self) -> None:
        self.assertFailsBeforeMutation({"schema_version": 1}, "repository")

    def test_wrong_repository_fails(self) -> None:
        self.assertFailsBeforeMutation(artifact(repository="Other/repo"), "repository")

    def test_wrong_issue_fails(self) -> None:
        bad = artifact(issue={"number": 37, "title": "wrong", "url": "url"})
        self.assertFailsBeforeMutation(bad, "issue")

    def test_wrong_or_stale_base_sha_fails(self) -> None:
        bad = artifact(base={"branch": "main", "sha": "b" * 40})
        self.assertFailsBeforeMutation(bad, "base")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.write_artifact(artifact(), root)
            fake = FakeFinalizerRunner()
            fake.origin_base = "c" * 40
            with self.assertRaises(finalize_issue.FinalizerError) as context:
                finalize_issue.finalize(path, self.expected(), root, {}, fake)
        self.assertIn("stale base SHA", context.exception.message)

    def test_unexpected_branch_identity_fails(self) -> None:
        self.assertFailsBeforeMutation(artifact(branch="agent/issue-999"), "branch")

    def test_duplicate_open_pr_identity_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.write_artifact(artifact(), root)
            fake = FakeFinalizerRunner()
            fake.prs = [{"number": 1, "url": "one"}, {"number": 2, "url": "two"}]
            with self.assertRaises(finalize_issue.FinalizerError) as context:
                finalize_issue.finalize(path, self.expected(), root, {}, fake)
        self.assertIn("multiple open PRs", context.exception.message)

    def test_retry_same_artifact_does_not_duplicate_commit_branch_or_pr(self) -> None:
        payload = artifact()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = self.write_artifact(payload, root)
            fake = FakeFinalizerRunner()
            first = finalize_issue.finalize(path, self.expected(), root, {}, fake)
            second = finalize_issue.finalize(path, self.expected(), root, {}, fake)
        self.assertFalse(first["commit_reused"])
        self.assertTrue(second["commit_reused"])
        self.assertEqual(len([call for call in fake.calls if call[:2] == ["git", "commit-tree"]]), 1)
        self.assertEqual(len([call for call in fake.calls if call[:3] == ["git", "push", "origin"]]), 1)
        self.assertEqual(len([call for call in fake.calls if call[:3] == ["gh", "pr", "create"]]), 1)

    def test_worker_command_or_script_content_is_rejected(self) -> None:
        self.assertFailsBeforeMutation(artifact(finalizer_command="sh -c ./pwned"), "finalizer_command")
        self.assertFailsBeforeMutation(artifact(script="./pwned"), "script")

    def test_path_traversal_metadata_is_rejected(self) -> None:
        bad = artifact(change={"format": "git-diff-binary", "changed_paths": ["../outside"], "patch": "diff"})
        self.assertFailsBeforeMutation(bad, "unsafe changed path")

    def test_malicious_patch_content_is_inert_data(self) -> None:
        payload = artifact(
            change={
                "format": "git-diff-binary",
                "changed_paths": ["tools/pwn.sh"],
                "patch": "diff --git a/tools/pwn.sh b/tools/pwn.sh\nnew file mode 100755\nindex 0000000..1111111\n--- /dev/null\n+++ b/tools/pwn.sh\n@@ -0,0 +1,2 @@\n+#!/bin/sh\n+sh -c 'echo pwned'\n",
            }
        )
        fake = FakeFinalizerRunner()
        fake.git_diff_paths = "tools/pwn.sh\n"
        result, fake = self.run_finalize(payload, fake)
        self.assertEqual(result["status"], "success")
        self.assertFalse(fake.executed_worker_content)
        self.assertFalse(any("tools/pwn.sh" in call for call in fake.calls))

    def test_provenance_is_preserved(self) -> None:
        result, fake = self.run_finalize(artifact())
        self.assertEqual(result["provenance"]["worker_run"], "issue-36-test")
        self.assertEqual(result["provenance"]["worker_verification"], "bash tools/ci/verify.sh")
        self.assertIn("Issue: #36", fake.last_commit_message)
        self.assertIn(f"Base-SHA: {BASE_SHA}", fake.last_commit_message)
        self.assertIn("Worker-Run: issue-36-test", fake.last_commit_message)


if __name__ == "__main__":
    unittest.main()
