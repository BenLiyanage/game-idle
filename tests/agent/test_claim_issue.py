#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "agent" / "claim_issue.py"
SPEC = importlib.util.spec_from_file_location("claim_issue", MODULE_PATH)
claim_issue = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["claim_issue"] = claim_issue
SPEC.loader.exec_module(claim_issue)


class FakeClaimRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.issue_state = "OPEN"
        self.issue_labels = [claim_issue.SELECTED_LABEL]
        self.in_progress_issues: list[int] = []

    def __call__(self, args: list[str], cwd: Path | None = None) -> claim_issue.CommandResult:
        del cwd
        self.calls.append(args)
        if args[:3] == ["gh", "issue", "view"]:
            payload = {
                "number": int(args[3]),
                "state": self.issue_state,
                "labels": [{"name": label} for label in self.issue_labels],
            }
            return claim_issue.CommandResult(args, 0, json.dumps(payload), "")
        if args[:3] == ["gh", "issue", "list"]:
            payload = [{"number": number} for number in self.in_progress_issues]
            return claim_issue.CommandResult(args, 0, json.dumps(payload), "")
        if args[:3] in (["gh", "issue", "comment"], ["gh", "issue", "edit"]):
            return claim_issue.CommandResult(args, 0, "", "")
        return claim_issue.CommandResult(args, 1, "", f"unexpected command: {args}")


class ClaimIssueTests(unittest.TestCase):
    def test_claims_only_current_selected_open_issue(self) -> None:
        fake = FakeClaimRunner()
        result = claim_issue.claim_issue(8, "BenLiyanage/game-idle", {}, fake)
        self.assertTrue(result["claimed"])
        self.assertTrue(any(call[:3] == ["gh", "issue", "view"] and call[3] == "8" for call in fake.calls))
        self.assertTrue(any(call[:3] == ["gh", "issue", "edit"] for call in fake.calls))

    def test_existing_wip_prevents_second_claim(self) -> None:
        fake = FakeClaimRunner()
        fake.in_progress_issues = [12]
        result = claim_issue.claim_issue(8, "BenLiyanage/game-idle", {}, fake)
        self.assertFalse(result["claimed"])
        self.assertEqual(result["reason"], "existing_wip")
        self.assertFalse(any(call[:3] == ["gh", "issue", "edit"] for call in fake.calls))

    def test_missing_selected_label_does_not_claim(self) -> None:
        fake = FakeClaimRunner()
        fake.issue_labels = ["groomed"]
        result = claim_issue.claim_issue(8, "BenLiyanage/game-idle", {}, fake)
        self.assertFalse(result["claimed"])
        self.assertEqual(result["reason"], "selected_label_missing")
        self.assertEqual(len([call for call in fake.calls if call[:2] == ["gh", "issue"]]), 1)


if __name__ == "__main__":
    unittest.main()
