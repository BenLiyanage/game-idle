#!/usr/bin/env python3
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class DevEngineRunnerWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (ROOT / ".github" / "workflows" / "selected-issue-dev-engine.yml").read_text(encoding="utf-8")
        self.control = (ROOT / "tools" / "runner" / "dev_engine_runner.sh").read_text(encoding="utf-8")

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
        self.assertIn('tools/agent/run_issue.sh "${{ needs.claim.outputs.issue-number }}"', self.workflow)

    def test_workflow_uses_explicit_minimum_permissions(self) -> None:
        self.assertIn("permissions:", self.workflow)
        self.assertIn("contents: write", self.workflow)
        self.assertIn("issues: write", self.workflow)
        self.assertIn("pull-requests: write", self.workflow)

    def test_runner_control_is_narrow_and_identity_checked(self) -> None:
        self.assertIn("start|stop|status", self.control)
        self.assertIn("DEV_ENGINE_RUNNER_DIR", self.control)
        self.assertIn("DEV_ENGINE_RUNNER_NAME", self.control)
        self.assertIn("runner identity mismatch", self.control)
        self.assertIn("runner repository mismatch", self.control)
        self.assertIn("actions/runners", self.control)
        self.assertIn("status, busy", self.control)
        self.assertNotIn("systemctl", self.control)
        self.assertNotIn("launchctl", self.control)
        self.assertNotIn("sudo", self.control)


if __name__ == "__main__":
    unittest.main()
