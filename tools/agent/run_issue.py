#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


EXIT_SUCCESS = 0
EXIT_BLOCKED = 10
EXIT_VERIFICATION_FAILED = 20
EXIT_IMPLEMENTATION_FAILED = 30
EXIT_INFRASTRUCTURE_FAILED = 40
EXIT_USAGE = 64

DEFAULT_BRANCH_PREFIX = "agent/issue-"
DEFAULT_BASE_BRANCH = "main"
DEFAULT_SANDBOX = "workspace-write"
DEFAULT_APPROVAL_POLICY = "never"
DEFAULT_REASONING_CONFIG_KEY = "model_reasoning_effort"


class WorkerError(Exception):
    def __init__(self, status: str, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.exit_code = exit_code


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class Issue:
    number: int
    title: str
    body: str
    url: str
    state: str


@dataclass(frozen=True)
class Layout:
    repo_root: Path
    worktree_root: Path
    branch: str
    worktree: Path
    base_branch: str
    result_dir: Path
    result_path: Path


def run_command(args: list[str], cwd: Path | None = None, input_text: str | None = None) -> CommandResult:
    completed = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return CommandResult(args, completed.returncode, completed.stdout, completed.stderr)


def require_success(result: CommandResult, status: str, exit_code: int, action: str) -> CommandResult:
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise WorkerError(status, f"{action} failed: {details}", exit_code)
    return result


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def validate_issue_number(raw: str | None) -> int:
    if raw is None or not re.fullmatch(r"[1-9][0-9]*", raw):
        raise WorkerError("usage_error", "usage: tools/agent/run_issue.sh <issue-number> [--dry-run]", EXIT_USAGE)
    return int(raw)


def parse_repo_from_remote(remote_url: str) -> str:
    remote_url = remote_url.strip()
    patterns = [
        r"^https://github\.com/([^/]+/[^/.]+)(?:\.git)?$",
        r"^git@github\.com:([^/]+/[^/.]+)(?:\.git)?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, remote_url)
        if match:
            return match.group(1)
    raise WorkerError("infrastructure_failed", f"cannot determine GitHub repository from origin URL: {remote_url}", EXIT_INFRASTRUCTURE_FAILED)


def github_repo(repo_root: Path, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> str:
    if env.get("CODEX_AGENT_REPO"):
        return env["CODEX_AGENT_REPO"]
    result = require_success(
        command_runner([env.get("CODEX_AGENT_GIT_BIN", "git"), "config", "--get", "remote.origin.url"], cwd=repo_root),
        "infrastructure_failed",
        EXIT_INFRASTRUCTURE_FAILED,
        "reading origin URL",
    )
    return parse_repo_from_remote(result.stdout)


def branch_for_issue(issue_number: int, env: dict[str, str]) -> str:
    prefix = env.get("CODEX_AGENT_BRANCH_PREFIX", DEFAULT_BRANCH_PREFIX)
    return f"{prefix}{issue_number}"


def build_layout(repo_root: Path, issue_number: int, env: dict[str, str]) -> Layout:
    branch = branch_for_issue(issue_number, env)
    safe_branch = branch.replace("/", "-")
    worktree_root = Path(env.get("CODEX_AGENT_WORKTREE_ROOT", repo_root / ".worktrees")).expanduser()
    worktree = worktree_root / safe_branch
    base_branch = env.get("CODEX_AGENT_BASE_BRANCH", DEFAULT_BASE_BRANCH)
    result_dir = Path(env.get("CODEX_AGENT_RESULT_DIR", repo_root / ".codex-agent" / f"issue-{issue_number}")).expanduser()
    result_path = Path(env.get("CODEX_AGENT_RESULT_PATH", result_dir / "result.json")).expanduser()
    return Layout(repo_root, worktree_root, branch, worktree, base_branch, result_dir, result_path)


def fetch_issue(issue_number: int, repo: str, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> Issue:
    gh = env.get("CODEX_AGENT_GH_BIN", "gh")
    result = require_success(
        command_runner(
            [gh, "issue", "view", str(issue_number), "--repo", repo, "--json", "number,title,body,url,state"]
        ),
        "infrastructure_failed",
        EXIT_INFRASTRUCTURE_FAILED,
        f"fetching issue #{issue_number}",
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise WorkerError("infrastructure_failed", f"gh returned invalid issue JSON: {exc}", EXIT_INFRASTRUCTURE_FAILED)
    issue = Issue(
        number=int(payload["number"]),
        title=str(payload.get("title") or ""),
        body=str(payload.get("body") or ""),
        url=str(payload.get("url") or ""),
        state=str(payload.get("state") or ""),
    )
    if issue.state.upper() != "OPEN":
        raise WorkerError("infrastructure_failed", f"issue #{issue_number} is not open: {issue.state}", EXIT_INFRASTRUCTURE_FAILED)
    return issue


def worktrees(repo_root: Path, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> dict[str, str]:
    git = env.get("CODEX_AGENT_GIT_BIN", "git")
    result = require_success(
        command_runner([git, "worktree", "list", "--porcelain"], cwd=repo_root),
        "infrastructure_failed",
        EXIT_INFRASTRUCTURE_FAILED,
        "listing worktrees",
    )
    entries: dict[str, str] = {}
    current_path: str | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = line.removeprefix("worktree ")
        elif line.startswith("branch refs/heads/") and current_path:
            entries[line.removeprefix("branch refs/heads/")] = current_path
    return entries


def branch_exists(repo_root: Path, branch: str, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> bool:
    git = env.get("CODEX_AGENT_GIT_BIN", "git")
    result = command_runner([git, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo_root)
    return result.returncode == 0


def remote_branch_exists(repo_root: Path, branch: str, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> bool:
    git = env.get("CODEX_AGENT_GIT_BIN", "git")
    result = command_runner([git, "show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"], cwd=repo_root)
    return result.returncode == 0


def ensure_worktree(layout: Layout, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> Path:
    git = env.get("CODEX_AGENT_GIT_BIN", "git")
    require_success(
        command_runner([git, "fetch", "origin", layout.base_branch], cwd=layout.repo_root),
        "infrastructure_failed",
        EXIT_INFRASTRUCTURE_FAILED,
        f"synchronizing origin/{layout.base_branch}",
    )
    existing = worktrees(layout.repo_root, env, command_runner)
    if layout.branch in existing:
        return Path(existing[layout.branch])
    if not branch_exists(layout.repo_root, layout.branch, env, command_runner):
        if remote_branch_exists(layout.repo_root, layout.branch, env, command_runner):
            require_success(
                command_runner([git, "branch", "--track", layout.branch, f"origin/{layout.branch}"], cwd=layout.repo_root),
                "infrastructure_failed",
                EXIT_INFRASTRUCTURE_FAILED,
                f"tracking origin/{layout.branch}",
            )
        else:
            require_success(
                command_runner([git, "branch", layout.branch, f"origin/{layout.base_branch}"], cwd=layout.repo_root),
                "infrastructure_failed",
                EXIT_INFRASTRUCTURE_FAILED,
                f"creating branch {layout.branch}",
            )
    if layout.worktree.exists():
        raise WorkerError("infrastructure_failed", f"worktree path already exists but is not registered: {layout.worktree}", EXIT_INFRASTRUCTURE_FAILED)
    require_success(
        command_runner([git, "worktree", "add", str(layout.worktree), layout.branch], cwd=layout.repo_root),
        "infrastructure_failed",
        EXIT_INFRASTRUCTURE_FAILED,
        f"creating worktree {layout.worktree}",
    )
    return layout.worktree


def codex_command(layout: Layout, env: dict[str, str], prompt_path: Path, schema_path: Path, output_path: Path) -> list[str]:
    command = [
        env.get("CODEX_AGENT_CODEX_BIN", "codex"),
        "exec",
        "--cd",
        str(layout.worktree),
        "--sandbox",
        env.get("CODEX_AGENT_SANDBOX", DEFAULT_SANDBOX),
        "--ask-for-approval",
        env.get("CODEX_AGENT_APPROVAL_POLICY", DEFAULT_APPROVAL_POLICY),
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(output_path),
    ]
    if env.get("CODEX_AGENT_MODEL"):
        command.extend(["--model", env["CODEX_AGENT_MODEL"]])
    if env.get("CODEX_AGENT_REASONING"):
        key = env.get("CODEX_AGENT_REASONING_CONFIG_KEY", DEFAULT_REASONING_CONFIG_KEY)
        command.extend(["--config", f'{key}="{env["CODEX_AGENT_REASONING"]}"'])
    command.append("-")
    return command


def prompt_for_issue(issue: Issue, worktree: Path, agents_text: str) -> str:
    return f"""Implement GitHub issue #{issue.number} in this repository worktree:
{worktree}

Issue URL:
{issue.url}

Issue title:
{issue.title}

Issue body:
{issue.body}

Repository contract from AGENTS.md:
{agents_text}

Task contract:
- Read and obey AGENTS.md before changing files.
- Treat the selected GitHub issue above as the canonical implementation specification.
- Preserve the issue scope and acceptance criteria.
- Stop on protected or blocking product/architecture decisions under AGENTS.md.
- Do not select, groom, prioritize, or implement any other issue.
- Do not implement issue #6 or issue #7 unless issue #{issue.number} explicitly requires a tiny interface reference.
- Run bash tools/ci/verify.sh before claiming success.
- Produce one coherent pull-request worth of work.
- Never merge a pull request.

Final response contract:
Return JSON matching the provided schema. Use status "blocked" only for a genuine protected or blocking decision. Use status "failure" if implementation could not be completed for other reasons. Use status "success" only when implementation and required verification are complete.
"""


def write_schema(path: Path) -> None:
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "summary", "blockers"],
        "properties": {
            "status": {"type": "string", "enum": ["success", "blocked", "failure"]},
            "summary": {"type": "string"},
            "blockers": {"type": "array", "items": {"type": "string"}},
        },
    }
    path.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")


def read_codex_result(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerError("implementation_failed", f"Codex did not produce valid result JSON: {exc}", EXIT_IMPLEMENTATION_FAILED)
    status = payload.get("status")
    if status not in {"success", "blocked", "failure"}:
        raise WorkerError("implementation_failed", f"Codex result has invalid status: {status}", EXIT_IMPLEMENTATION_FAILED)
    return payload


def run_verification(worktree: Path, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> CommandResult:
    result = command_runner(["bash", "tools/ci/verify.sh"], cwd=worktree)
    if result.returncode != 0:
        raise WorkerError("verification_failed", "repository verification failed", EXIT_VERIFICATION_FAILED)
    return result


def git_has_changes(worktree: Path, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> bool:
    git = env.get("CODEX_AGENT_GIT_BIN", "git")
    result = require_success(
        command_runner([git, "status", "--porcelain"], cwd=worktree),
        "infrastructure_failed",
        EXIT_INFRASTRUCTURE_FAILED,
        "checking worktree status",
    )
    return bool(result.stdout.strip())


def commit_and_push(issue: Issue, layout: Layout, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> None:
    git = env.get("CODEX_AGENT_GIT_BIN", "git")
    if not git_has_changes(layout.worktree, env, command_runner):
        raise WorkerError("implementation_failed", "Codex completed but produced no committable changes", EXIT_IMPLEMENTATION_FAILED)
    require_success(command_runner([git, "add", "-A"], cwd=layout.worktree), "infrastructure_failed", EXIT_INFRASTRUCTURE_FAILED, "staging changes")
    require_success(
        command_runner([git, "commit", "-m", f"Implement issue #{issue.number} local worker"], cwd=layout.worktree),
        "infrastructure_failed",
        EXIT_INFRASTRUCTURE_FAILED,
        "committing changes",
    )
    require_success(
        command_runner([git, "push", "-u", "origin", layout.branch], cwd=layout.worktree),
        "infrastructure_failed",
        EXIT_INFRASTRUCTURE_FAILED,
        f"pushing {layout.branch}",
    )


def load_pr_template(repo_root: Path) -> str:
    path = repo_root / ".github" / "pull_request_template.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def pr_body(issue: Issue, codex_result: dict[str, Any], verify_result: CommandResult, template: str) -> str:
    body = template
    replacements = {
        "Closes #<issue>": f"Closes #{issue.number}",
        "## Scope Summary\n": f"## Scope Summary\n\n{codex_result.get('summary', '').strip()}\n",
        "```text\n```": "```text\nbash tools/ci/verify.sh\n```",
        "## Test Results\n": f"## Test Results\n\n`bash tools/ci/verify.sh` exited 0.\n",
        "## Checks That Could Not Run\n": "## Checks That Could Not Run\n\nNone.\n",
        "## Deferred Work\n": "## Deferred Work\n\nEvent orchestration remains deferred to #6. Security hardening remains deferred to #7.\n",
        "## Human Verification Steps\n": "## Human Verification Steps\n\nReview the generated worker behavior and CI result before merging.\n",
    }
    for old, new in replacements.items():
        body = body.replace(old, new)
    if "## Acceptance-Criteria Mapping\n\n- [ ]" in body:
        body = body.replace(
            "## Acceptance-Criteria Mapping\n\n- [ ]",
            "## Acceptance-Criteria Mapping\n\n- [x] Repository-owned explicit issue worker added.\n- [x] Deterministic branch/worktree, Codex invocation, verification, result, push, and PR behavior documented and tested.",
        )
    if "## Risks and Decisions\n" in body:
        body = body.replace(
            "## Risks and Decisions\n",
            "## Risks and Decisions\n\nUses current laptop `gh`/`git` authentication for publishing; stronger credential separation remains #7.\n",
        )
    return body


def open_or_update_pr(issue: Issue, layout: Layout, repo: str, body: str, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> str:
    gh = env.get("CODEX_AGENT_GH_BIN", "gh")
    list_result = require_success(
        command_runner(
            [gh, "pr", "list", "--repo", repo, "--head", layout.branch, "--base", layout.base_branch, "--state", "open", "--json", "number,url"]
        ),
        "infrastructure_failed",
        EXIT_INFRASTRUCTURE_FAILED,
        "checking existing pull requests",
    )
    try:
        prs = json.loads(list_result.stdout)
    except json.JSONDecodeError as exc:
        raise WorkerError("infrastructure_failed", f"gh returned invalid PR JSON: {exc}", EXIT_INFRASTRUCTURE_FAILED)
    body_path = layout.result_dir / "pr-body.md"
    body_path.write_text(body, encoding="utf-8")
    if len(prs) > 1:
        raise WorkerError("infrastructure_failed", f"multiple open PRs found for {layout.branch}", EXIT_INFRASTRUCTURE_FAILED)
    if len(prs) == 1:
        number = str(prs[0]["number"])
        require_success(
            command_runner([gh, "pr", "edit", number, "--repo", repo, "--body-file", str(body_path)]),
            "infrastructure_failed",
            EXIT_INFRASTRUCTURE_FAILED,
            f"updating PR #{number}",
        )
        return str(prs[0].get("url") or number)
    create_result = require_success(
        command_runner(
            [
                gh,
                "pr",
                "create",
                "--repo",
                repo,
                "--base",
                layout.base_branch,
                "--head",
                layout.branch,
                "--title",
                f"Implement issue #{issue.number}: {issue.title}",
                "--body-file",
                str(body_path),
            ]
        ),
        "infrastructure_failed",
        EXIT_INFRASTRUCTURE_FAILED,
        "creating pull request",
    )
    return create_result.stdout.strip()


def write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))


def command_line(command: Iterable[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def dry_run_payload(issue: Issue, layout: Layout, repo: str, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> dict[str, Any]:
    existing_worktrees = worktrees(layout.repo_root, env, command_runner)
    gh = env.get("CODEX_AGENT_GH_BIN", "gh")
    pr_result = command_runner(
        [gh, "pr", "list", "--repo", repo, "--head", layout.branch, "--base", layout.base_branch, "--state", "open", "--json", "number,url"]
    )
    existing_prs: list[Any] = []
    if pr_result.returncode == 0 and pr_result.stdout.strip():
        existing_prs = json.loads(pr_result.stdout)
    prompt_path = layout.result_dir / "prompt.md"
    schema_path = layout.result_dir / "codex-result.schema.json"
    output_path = layout.result_dir / "codex-result.json"
    return {
        "status": "dry_run",
        "issue": {"number": issue.number, "title": issue.title, "url": issue.url, "state": issue.state},
        "branch": layout.branch,
        "branch_exists": branch_exists(layout.repo_root, layout.branch, env, command_runner),
        "worktree": str(layout.worktree),
        "worktree_reused": layout.branch in existing_worktrees,
        "verification_command": "bash tools/ci/verify.sh",
        "codex_command": command_line(codex_command(layout, env, prompt_path, schema_path, output_path)),
        "existing_prs": existing_prs,
        "repo": repo,
    }


def main(argv: list[str] | None = None, command_runner: Callable[..., CommandResult] = run_command, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Codex against one explicit GitHub issue.")
    parser.add_argument("issue_number", nargs="?")
    parser.add_argument("--dry-run", action="store_true", help="resolve metadata and planned commands without invoking Codex or mutating GitHub")
    args = parser.parse_args(argv)
    env = dict(os.environ if env is None else env)
    try:
        issue_number = validate_issue_number(args.issue_number)
        repo_root = repo_root_from_script()
        layout = build_layout(repo_root, issue_number, env)
        repo = github_repo(repo_root, env, command_runner)
        issue = fetch_issue(issue_number, repo, env, command_runner)
        if args.dry_run:
            payload = dry_run_payload(issue, layout, repo, env, command_runner)
            write_result(layout.result_path, payload)
            return EXIT_SUCCESS

        layout.result_dir.mkdir(parents=True, exist_ok=True)
        actual_worktree = ensure_worktree(layout, env, command_runner)
        layout = Layout(layout.repo_root, layout.worktree_root, layout.branch, actual_worktree, layout.base_branch, layout.result_dir, layout.result_path)
        agents_text = (layout.worktree / "AGENTS.md").read_text(encoding="utf-8")
        prompt_path = layout.result_dir / "prompt.md"
        schema_path = layout.result_dir / "codex-result.schema.json"
        output_path = layout.result_dir / "codex-result.json"
        prompt = prompt_for_issue(issue, layout.worktree, agents_text)
        prompt_path.write_text(prompt, encoding="utf-8")
        write_schema(schema_path)
        codex_result = command_runner(codex_command(layout, env, prompt_path, schema_path, output_path), cwd=layout.worktree, input_text=prompt)
        if codex_result.returncode != 0:
            raise WorkerError("implementation_failed", (codex_result.stderr or codex_result.stdout).strip(), EXIT_IMPLEMENTATION_FAILED)
        parsed_codex_result = read_codex_result(output_path)
        if parsed_codex_result["status"] == "blocked":
            write_result(layout.result_path, {"status": "blocked", "issue": issue.number, "branch": layout.branch, "worktree": str(layout.worktree), "blockers": parsed_codex_result.get("blockers", [])})
            return EXIT_BLOCKED
        if parsed_codex_result["status"] == "failure":
            raise WorkerError("implementation_failed", parsed_codex_result.get("summary", "Codex reported failure"), EXIT_IMPLEMENTATION_FAILED)
        verify_result = run_verification(layout.worktree, env, command_runner)
        commit_and_push(issue, layout, env, command_runner)
        body = pr_body(issue, parsed_codex_result, verify_result, load_pr_template(layout.worktree))
        pr_url = open_or_update_pr(issue, layout, repo, body, env, command_runner)
        write_result(layout.result_path, {"status": "success", "issue": issue.number, "branch": layout.branch, "worktree": str(layout.worktree), "pr": pr_url})
        return EXIT_SUCCESS
    except WorkerError as exc:
        issue = args.issue_number if args.issue_number else None
        fallback_root = repo_root_from_script()
        result_path = Path(env.get("CODEX_AGENT_RESULT_PATH", fallback_root / ".codex-agent" / "result.json"))
        write_result(result_path, {"status": exc.status, "issue": issue, "message": exc.message})
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
