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
        self.lifecycle_labels = [claim_issue.SELECTED_LABEL, claim_issue.IN_PROGRESS_LABEL, claim_issue.FAILED_LABEL]

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
        if args[:3] == ["gh", "label", "list"]:
            return claim_issue.CommandResult(
                args,
                0,
                json.dumps([{"name": label} for label in self.lifecycle_labels]),
                "",
            )
        if args[:3] == ["gh", "issue", "edit"]:
            added_label = args[args.index("--add-label") + 1]
            if added_label == claim_issue.IN_PROGRESS_LABEL:
                self.issue_labels = [claim_issue.IN_PROGRESS_LABEL]
            elif added_label == claim_issue.FAILED_LABEL:
                self.issue_labels = [claim_issue.FAILED_LABEL]
            return claim_issue.CommandResult(args, 0, "", "")
        if args[:3] == ["gh", "issue", "comment"]:
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

    def test_missing_in_progress_label_fails_before_issue_mutation(self) -> None:
        fake = FakeClaimRunner()
        fake.lifecycle_labels = [claim_issue.SELECTED_LABEL]
        with self.assertRaisesRegex(claim_issue.ClaimError, "missing"):
            claim_issue.claim_issue(8, "BenLiyanage/game-idle", {}, fake)
        self.assertFalse(any(call[:3] == ["gh", "issue", "edit"] for call in fake.calls))

    def test_ambiguous_transition_failure_reconciles_to_failed_without_comment(self) -> None:
        class FailingEdit(FakeClaimRunner):
            def __call__(self, args: list[str], cwd: Path | None = None) -> claim_issue.CommandResult:
                if args[:3] == ["gh", "issue", "edit"] and "--add-label" in args:
                    if len([call for call in self.calls if call[:3] == ["gh", "issue", "edit"]]) == 0:
                        self.calls.append(args)
                        return claim_issue.CommandResult(args, 1, "", "failed to update 1 issue\nretry later")
                    return super().__call__(args, cwd)
                return super().__call__(args, cwd)

        fake = FailingEdit()
        result = claim_issue.claim_issue(8, "BenLiyanage/game-idle", {}, fake)
        self.assertFalse(result["claimed"])
        self.assertEqual(result["reason"], "claim_failed")
        edits = [call for call in fake.calls if call[:3] == ["gh", "issue", "edit"]]
        self.assertEqual(len(edits), 2)
        self.assertFalse(any(call[:3] == ["gh", "issue", "comment"] for call in fake.calls))

    def test_comment_failure_does_not_unclaim_verified_transition(self) -> None:
        class FailingComment(FakeClaimRunner):
            def __call__(self, args: list[str], cwd: Path | None = None) -> claim_issue.CommandResult:
                if args[:3] == ["gh", "issue", "comment"]:
                    self.calls.append(args)
                    return claim_issue.CommandResult(args, 1, "", "comment unavailable")
                return super().__call__(args, cwd)

        fake = FailingComment()
        result = claim_issue.claim_issue(8, "BenLiyanage/game-idle", {}, fake)
        self.assertTrue(result["claimed"])
        self.assertIn("warning", result)

    def test_reported_transition_failure_with_completed_postcondition_claims(self) -> None:
        class PartialEdit(FakeClaimRunner):
            def __call__(self, args: list[str], cwd: Path | None = None) -> claim_issue.CommandResult:
                if args[:3] == ["gh", "issue", "edit"]:
                    self.issue_labels = [claim_issue.IN_PROGRESS_LABEL]
                    self.calls.append(args)
                    return claim_issue.CommandResult(args, 1, "", "request timed out")
                return super().__call__(args, cwd)

        result = claim_issue.claim_issue(8, "BenLiyanage/game-idle", {}, PartialEdit())
        self.assertTrue(result["claimed"])


if __name__ == "__main__":
    unittest.main()
