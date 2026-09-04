#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXIT_SUCCESS = 0
EXIT_INFRASTRUCTURE_FAILED = 40
EXIT_USAGE = 64
SELECTED_LABEL = "selected-for-development"
IN_PROGRESS_LABEL = "in-progress"
CLAIM_MARKER = "<!-- dev-engine-claim:v1 -->"


class ClaimError(Exception):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


def run_command(args: list[str], cwd: Path | None = None) -> CommandResult:
    completed = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        check=False,
    )
    return CommandResult(args, completed.returncode, completed.stdout, completed.stderr)


def validate_issue_number(raw: str) -> int:
    if not re.fullmatch(r"[1-9][0-9]*", raw):
        raise ClaimError("issue number must be a positive integer", EXIT_USAGE)
    return int(raw)


def require_success(result: CommandResult, action: str) -> CommandResult:
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise ClaimError(f"{action} failed: {details}", EXIT_INFRASTRUCTURE_FAILED)
    return result


def gh_bin(env: dict[str, str]) -> str:
    return env.get("CODEX_AGENT_GH_BIN", "gh")


def fetch_issue(
    issue_number: int, repo: str, env: dict[str, str], command_runner: Callable[..., CommandResult]
) -> dict[str, Any]:
    result = require_success(
        command_runner(
            [
                gh_bin(env),
                "issue",
                "view",
                str(issue_number),
                "--repo",
                repo,
                "--json",
                "number,state,labels",
            ]
        ),
        f"fetching issue #{issue_number}",
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ClaimError(f"gh returned invalid issue JSON: {exc}", EXIT_INFRASTRUCTURE_FAILED) from exc
    if int(payload.get("number", 0)) != issue_number:
        raise ClaimError(f"requested issue #{issue_number}, got #{payload.get('number')}", EXIT_INFRASTRUCTURE_FAILED)
    return payload


def issue_has_label(issue: dict[str, Any], label: str) -> bool:
    labels = issue.get("labels", [])
    return any(item.get("name") == label for item in labels if isinstance(item, dict))


def open_in_progress_issues(
    selected_issue: int, repo: str, env: dict[str, str], command_runner: Callable[..., CommandResult]
) -> list[int]:
    result = require_success(
        command_runner(
            [
                gh_bin(env),
                "issue",
                "list",
                "--repo",
                repo,
                "--state",
                "open",
                "--label",
                IN_PROGRESS_LABEL,
                "--json",
                "number",
            ]
        ),
        "listing in-progress issues",
    )
    try:
        issues = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ClaimError(f"gh returned invalid issue list JSON: {exc}", EXIT_INFRASTRUCTURE_FAILED) from exc
    return [int(item["number"]) for item in issues if int(item["number"]) != selected_issue]


def comment(
    issue_number: int, repo: str, body: str, env: dict[str, str], command_runner: Callable[..., CommandResult]
) -> None:
    require_success(
        command_runner([gh_bin(env), "issue", "comment", str(issue_number), "--repo", repo, "--body", body]),
        f"commenting on issue #{issue_number}",
    )


def move_to_in_progress(
    issue_number: int, repo: str, env: dict[str, str], command_runner: Callable[..., CommandResult]
) -> None:
    require_success(
        command_runner(
            [
                gh_bin(env),
                "issue",
                "edit",
                str(issue_number),
                "--repo",
                repo,
                "--remove-label",
                SELECTED_LABEL,
                "--add-label",
                IN_PROGRESS_LABEL,
            ]
        ),
        f"moving issue #{issue_number} to in-progress",
    )


def write_github_outputs(path: str | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        for key, value in payload.items():
            handle.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")


def claim_issue(
    issue_number: int, repo: str, env: dict[str, str], command_runner: Callable[..., CommandResult]
) -> dict[str, Any]:
    issue = fetch_issue(issue_number, repo, env, command_runner)
    if issue.get("state") != "OPEN":
        return {"claimed": False, "issue_number": issue_number, "reason": "issue_not_open"}
    if not issue_has_label(issue, SELECTED_LABEL):
        return {"claimed": False, "issue_number": issue_number, "reason": "selected_label_missing"}
    existing_wip = open_in_progress_issues(issue_number, repo, env, command_runner)
    if existing_wip:
        comment(
            issue_number,
            repo,
            "Dev Engine did not claim this issue because another issue is already labeled in-progress.",
            env,
            command_runner,
        )
        return {
            "claimed": False,
            "issue_number": issue_number,
            "reason": "existing_wip",
            "existing_wip": existing_wip,
        }
    comment(
        issue_number,
        repo,
        f"{CLAIM_MARKER}Dev Engine claimed this issue for the supervised host-native runner.",
        env,
        command_runner,
    )
    move_to_in_progress(issue_number, repo, env, command_runner)
    return {"claimed": True, "issue_number": issue_number, "reason": "claimed"}


def main(
    argv: list[str] | None = None,
    command_runner: Callable[..., CommandResult] = run_command,
    env: dict[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Claim one selected issue for the supervised Dev Engine runner.")
    parser.add_argument("issue_number")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--github-output")
    args = parser.parse_args(argv)
    env = dict(os.environ if env is None else env)
    try:
        issue_number = validate_issue_number(args.issue_number)
        payload = claim_issue(issue_number, args.repo, env, command_runner)
        write_github_outputs(
            args.github_output,
            {"claimed": payload["claimed"], "issue_number": payload["issue_number"], "reason": payload["reason"]},
        )
        print(json.dumps(payload, sort_keys=True))
        return EXIT_SUCCESS
    except ClaimError as exc:
        payload = {"claimed": False, "issue_number": args.issue_number, "reason": "error", "message": exc.message}
        write_github_outputs(args.github_output, payload)
        print(json.dumps(payload, sort_keys=True))
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
