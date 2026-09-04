#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "tools" / "runner" / "dev_engine_runner.py"
SPEC = importlib.util.spec_from_file_location("dev_engine_runner", MODULE_PATH)
dev_engine_runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["dev_engine_runner"] = dev_engine_runner
SPEC.loader.exec_module(dev_engine_runner)


class DevEngineRunnerWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (ROOT / ".github" / "workflows" / "selected-issue-dev-engine.yml").read_text(encoding="utf-8")
        self.shell_wrapper = (ROOT / "tools" / "runner" / "dev_engine_runner.sh").read_text(encoding="utf-8")

    def test_workflow_is_issue_label_driven_not_pr_driven(self) -> None:
        self.assertIn("issues:", self.workflow)
        self.assertIn("types: [labeled]", self.workflow)
        self.assertNotIn("pull_request", self.workflow)
        self.assertNotIn("pull_request_target", self.workflow)

    def test_laptop_runner_job_requires_claimed_selected_issue(self) -> None:
        self.assertIn("github.event.label.name == 'selected-for-development'", self.workflow)
        self.assertIn("concurrency:", self.workflow)
        self.assertIn("group: dev-engine-issue-wip", self.workflow)
        self.assertIn("needs.claim.outputs.claimed == 'true'", self.workflow)
        self.assertIn("runs-on: [self-hosted, dev-engine]", self.workflow)
        self.assertIn("python3 tools/agent/claim_issue.py", self.workflow)
        self.assertIn('tools/agent/run_issue.sh "${{ needs.claim.outputs.issue-number }}"', self.workflow)

    def test_workflow_uses_job_scoped_minimum_permissions(self) -> None:
        self.assertIn("permissions: {}", self.workflow)
        self.assertIn("permissions:\n      contents: read\n      issues: write", self.workflow)
        self.assertIn(
            "permissions:\n      contents: write\n      issues: read\n      pull-requests: write",
            self.workflow,
        )

    def test_shell_control_wrapper_is_thin(self) -> None:
        self.assertIn("dev_engine_runner.py", self.shell_wrapper)
        self.assertNotIn("python3 -", self.shell_wrapper)
        self.assertNotIn("launchctl", self.shell_wrapper)
        self.assertNotIn("sudo", self.shell_wrapper)

    def test_runner_identity_accepts_bom_metadata_and_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner_dir = Path(tmp)
            (runner_dir / "svc.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            (runner_dir / "svc.sh").chmod(0o755)
            (runner_dir / ".runner").write_text(
                "\ufeff"
                + json.dumps(
                    {
                        "agentName": "game-idle-dev-engine",
                        "gitHubUrl": "https://github.com/BenLiyanage/game-idle",
                    }
                ),
                encoding="utf-8",
            )
            config = dev_engine_runner.resolve_config(
                {
                    "DEV_ENGINE_RUNNER_DIR": str(runner_dir),
                    "DEV_ENGINE_RUNNER_NAME": "game-idle-dev-engine",
                }
            )
            self.assertEqual(dev_engine_runner.validate_runner_identity(config), (runner_dir / "svc.sh").resolve())

            bad_config = dev_engine_runner.RunnerConfig(
                runner_dir=runner_dir,
                runner_name="other-runner",
                repo_url="https://github.com/BenLiyanage/game-idle",
            )
            with self.assertRaises(dev_engine_runner.RunnerControlError):
                dev_engine_runner.validate_runner_identity(bad_config)


if __name__ == "__main__":
    unittest.main()
