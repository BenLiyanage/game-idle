#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXIT_SUCCESS = 0
EXIT_BLOCKED = 10
EXIT_VALIDATION_FAILED = 20
EXIT_IMPLEMENTATION_FAILED = 30
EXIT_INFRASTRUCTURE_FAILED = 40
EXIT_CAPACITY = 50
EXIT_USAGE = 64

DEFAULT_BRANCH_PREFIX = "agent/issue-"
DEFAULT_BASE_BRANCH = "main"
DEFAULT_SANDBOX = "workspace-write"
DEFAULT_APPROVAL_POLICY = "never"
DEFAULT_REASONING_CONFIG_KEY = "model_reasoning_effort"
DEFAULT_MAX_REPAIR_ATTEMPTS = 1
CODEX_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status", "summary", "blockers"],
    "properties": {
        "status": {"type": "string", "enum": ["success", "blocked", "failure"]},
        "summary": {"type": "string"},
        "blockers": {"type": "array", "items": {"type": "string"}},
    },
}


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


@dataclass(frozen=True)
class CodexAttempt:
    command: CommandResult
    result: dict[str, Any]


@dataclass(frozen=True)
class ValidationSummary:
    status: str
    attempts: list[CommandResult]


def run_command(args: list[str], cwd: Path | None = None, input_text: str | None = None) -> CommandResult:
    completed = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        input=input_text,
        text=True,
        capture_output=True,
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
    raise WorkerError(
        "infrastructure_failed",
        f"cannot determine GitHub repository from origin URL: {remote_url}",
        EXIT_INFRASTRUCTURE_FAILED,
    )


def github_repo(repo_root: Path, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> str:
    if env.get("CODEX_AGENT_REPO"):
        return env["CODEX_AGENT_REPO"]
    git = env.get("CODEX_AGENT_GIT_BIN", "git")
    result = require_success(
        command_runner([git, "config", "--get", "remote.origin.url"], cwd=repo_root),
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
    result_dir = Path(
        env.get("CODEX_AGENT_RESULT_DIR", repo_root / ".codex-agent" / f"issue-{issue_number}")
    ).expanduser()
    result_path = Path(env.get("CODEX_AGENT_RESULT_PATH", result_dir / "result.json")).expanduser()
    return Layout(repo_root, worktree_root, branch, worktree, base_branch, result_dir, result_path)


def fetch_issue(
    issue_number: int, repo: str, env: dict[str, str], command_runner: Callable[..., CommandResult]
) -> Issue:
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
        raise WorkerError(
            "infrastructure_failed", f"gh returned invalid issue JSON: {exc}", EXIT_INFRASTRUCTURE_FAILED
        ) from exc
    issue = Issue(
        number=int(payload["number"]),
        title=str(payload.get("title") or ""),
        body=str(payload.get("body") or ""),
        url=str(payload.get("url") or ""),
        state=str(payload.get("state") or ""),
    )
    if issue.number != issue_number:
        raise WorkerError(
            "infrastructure_failed",
            f"requested issue #{issue_number}, but GitHub returned #{issue.number}",
            EXIT_INFRASTRUCTURE_FAILED,
        )
    if issue.state.upper() != "OPEN":
        raise WorkerError(
            "infrastructure_failed", f"issue #{issue_number} is not open: {issue.state}", EXIT_INFRASTRUCTURE_FAILED
        )
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


def branch_exists(
    repo_root: Path, branch: str, env: dict[str, str], command_runner: Callable[..., CommandResult]
) -> bool:
    git = env.get("CODEX_AGENT_GIT_BIN", "git")
    result = command_runner([git, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo_root)
    return result.returncode == 0


def remote_branch_exists(
    repo_root: Path, branch: str, env: dict[str, str], command_runner: Callable[..., CommandResult]
) -> bool:
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
                command_runner(
                    [git, "branch", "--track", layout.branch, f"origin/{layout.branch}"], cwd=layout.repo_root
                ),
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
        raise WorkerError(
            "infrastructure_failed",
            f"worktree path already exists but is not registered: {layout.worktree}",
            EXIT_INFRASTRUCTURE_FAILED,
        )
    require_success(
        command_runner([git, "worktree", "add", str(layout.worktree), layout.branch], cwd=layout.repo_root),
        "infrastructure_failed",
        EXIT_INFRASTRUCTURE_FAILED,
        f"creating worktree {layout.worktree}",
    )
    return layout.worktree


def codex_command(output_path: Path, env: dict[str, str]) -> list[str]:
    codex_bin = env.get("CODEX_AGENT_CODEX_BIN", "codex")
    if "node_modules/.bin/codex" in codex_bin or "/npm/" in codex_bin:
        raise WorkerError(
            "infrastructure_failed",
            "CODEX_AGENT_CODEX_BIN appears to reference an npm/container Codex CLI; use the host Codex installation",
            EXIT_INFRASTRUCTURE_FAILED,
        )
    command = [
        codex_bin,
        "exec",
        "--sandbox",
        env.get("CODEX_AGENT_SANDBOX", DEFAULT_SANDBOX),
        "--ask-for-approval",
        env.get("CODEX_AGENT_APPROVAL_POLICY", DEFAULT_APPROVAL_POLICY),
        "--output-schema",
        str(output_path.with_suffix(".schema.json")),
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


def render_template(path: Path, values: dict[str, object]) -> str:
    text = path.read_text(encoding="utf-8")
    for key, value in values.items():
        text = text.replace("{{ " + key + " }}", str(value))
    unreplaced = re.findall(r"{{\s*[-_a-zA-Z0-9]+\s*}}", text)
    if unreplaced:
        raise WorkerError(
            "infrastructure_failed",
            "prompt template has unresolved placeholders: " + ", ".join(sorted(set(unreplaced))),
            EXIT_INFRASTRUCTURE_FAILED,
        )
    return text


def prompt_templates(repo_root: Path) -> tuple[Path, Path]:
    prompt_dir = repo_root / "tools" / "agent" / "prompts"
    return prompt_dir / "implementation.md", prompt_dir / "repair.md"


def prompt_for_issue(issue: Issue, worktree: Path, agents_text: str, repo_root: Path | None = None) -> str:
    implementation_template, _ = prompt_templates(repo_root or repo_root_from_script())
    return render_template(
        implementation_template,
        {
            "issue_number": issue.number,
            "worktree": worktree,
            "issue_url": issue.url,
            "issue_title": issue.title,
            "issue_body": issue.body,
            "agents_text": agents_text,
        },
    )


def repair_prompt(issue: Issue, validation_result: CommandResult, repo_root: Path | None = None) -> str:
    output = (validation_result.stdout + validation_result.stderr).strip()
    _, repair_template = prompt_templates(repo_root or repo_root_from_script())
    return render_template(
        repair_template,
        {
            "issue_number": issue.number,
            "validation_command": command_line(validation_result.args),
            "validation_exit_code": validation_result.returncode,
            "validation_output": output[-12000:],
        },
    )


def write_schema(path: Path) -> None:
    path.write_text(json.dumps(CODEX_RESULT_SCHEMA, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_codex_result(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkerError(
            "implementation_failed", f"Codex did not produce valid result JSON: {exc}", EXIT_IMPLEMENTATION_FAILED
        ) from exc
    status = payload.get("status")
    if status not in {"success", "blocked", "failure"}:
        raise WorkerError(
            "implementation_failed", f"Codex result has invalid status: {status}", EXIT_IMPLEMENTATION_FAILED
        )
    return payload


def run_codex(
    layout: Layout,
    prompt: str,
    attempt_name: str,
    env: dict[str, str],
    command_runner: Callable[..., CommandResult],
) -> CodexAttempt:
    output_path = layout.result_dir / f"codex-{attempt_name}.txt"
    write_schema(output_path.with_suffix(".schema.json"))
    command = codex_command(output_path, env)
    result = command_runner(command, cwd=layout.worktree, input_text=prompt)
    transcript_path = layout.result_dir / f"codex-{attempt_name}-command-output.txt"
    transcript_path.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode == EXIT_CAPACITY:
        raise WorkerError("capacity", "Codex reported capacity exhaustion", EXIT_CAPACITY)
    if result.returncode != 0:
        raise WorkerError(
            "implementation_failed",
            f"Codex attempt {attempt_name} failed with exit {result.returncode}",
            EXIT_IMPLEMENTATION_FAILED,
        )
    parsed = read_codex_result(output_path)
    if parsed["status"] == "blocked":
        raise WorkerError("blocked", parsed.get("summary", "Codex reported blocked"), EXIT_BLOCKED)
    if parsed["status"] == "failure":
        raise WorkerError(
            "implementation_failed", parsed.get("summary", "Codex reported failure"), EXIT_IMPLEMENTATION_FAILED
        )
    return CodexAttempt(result, parsed)


def validation_command(env: dict[str, str]) -> list[str]:
    del env
    return ["bash", "tools/ci/verify.sh"]


def run_validation(worktree: Path, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> CommandResult:
    return command_runner(validation_command(env), cwd=worktree)


def is_missing_local_godot(validation: CommandResult) -> bool:
    output = validation.stdout + validation.stderr
    return (
        validation.returncode != 0
        and "Godot is unavailable." in output
        and "== godot ==" in output
        and "== headless import ==" not in output
    )


def bounded_implementation_loop(
    issue: Issue,
    layout: Layout,
    agents_text: str,
    env: dict[str, str],
    command_runner: Callable[..., CommandResult],
) -> tuple[CodexAttempt, ValidationSummary]:
    max_repairs = int(env.get("CODEX_AGENT_MAX_REPAIR_ATTEMPTS", str(DEFAULT_MAX_REPAIR_ATTEMPTS)))
    if max_repairs < 0 or max_repairs > 3:
        raise WorkerError("usage_error", "CODEX_AGENT_MAX_REPAIR_ATTEMPTS must be between 0 and 3", EXIT_USAGE)
    codex_result = run_codex(
        layout, prompt_for_issue(issue, layout.worktree, agents_text), "initial", env, command_runner
    )
    validations: list[CommandResult] = []
    for attempt in range(max_repairs + 1):
        validation = run_validation(layout.worktree, env, command_runner)
        validations.append(validation)
        validation_log = layout.result_dir / f"validation-{attempt + 1}.txt"
        validation_log.write_text(validation.stdout + validation.stderr, encoding="utf-8")
        if validation.returncode == 0:
            return codex_result, ValidationSummary("passed", validations)
        if is_missing_local_godot(validation):
            return codex_result, ValidationSummary("cloud_only_prerequisite_missing", validations)
        if attempt < max_repairs:
            codex_result = run_codex(
                layout, repair_prompt(issue, validation), f"repair-{attempt + 1}", env, command_runner
            )
    raise WorkerError(
        "validation_failed",
        f"local validation failed after {len(validations)} attempt(s)",
        EXIT_VALIDATION_FAILED,
    )


def git_has_changes(worktree: Path, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> bool:
    git = env.get("CODEX_AGENT_GIT_BIN", "git")
    result = require_success(
        command_runner([git, "status", "--porcelain"], cwd=worktree),
        "infrastructure_failed",
        EXIT_INFRASTRUCTURE_FAILED,
        "checking worktree status",
    )
    return bool(result.stdout.strip())


def commit_and_push(
    issue: Issue, layout: Layout, env: dict[str, str], command_runner: Callable[..., CommandResult]
) -> None:
    git = env.get("CODEX_AGENT_GIT_BIN", "git")
    if not git_has_changes(layout.worktree, env, command_runner):
        raise WorkerError(
            "implementation_failed", "Codex completed but produced no committable changes", EXIT_IMPLEMENTATION_FAILED
        )
    require_success(
        command_runner([git, "add", "-A"], cwd=layout.worktree),
        "infrastructure_failed",
        EXIT_INFRASTRUCTURE_FAILED,
        "staging changes",
    )
    require_success(
        command_runner([git, "commit", "-m", f"Implement issue #{issue.number}: {issue.title}"], cwd=layout.worktree),
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


def pr_body(
    issue: Issue, codex_result: CodexAttempt, validation: ValidationSummary, template: str, env: dict[str, str]
) -> str:
    body = template
    commands = [
        f"tools/agent/run_issue.sh {issue.number}",
        *[command_line(result.args) for result in validation.attempts],
    ]
    test_results = {
        "passed": "Canonical verification passed locally.",
        "cloud_only_prerequisite_missing": "Canonical verification ran locally and stopped at a missing device prerequisite; GitHub-hosted CI remains authoritative for pinned Godot execution.",
    }.get(validation.status, "Canonical verification did not pass locally.")
    model = env.get("CODEX_AGENT_MODEL", "<host Codex default>")
    reasoning = env.get("CODEX_AGENT_REASONING", "<host Codex default>")
    summary = str(codex_result.result.get("summary") or "").strip()
    blockers = codex_result.result.get("blockers") or []
    replacements = {
        "Closes #<issue>": f"Closes #{issue.number}",
        "## Scope Summary\n": f"## Scope Summary\n\n{summary or 'Implemented the explicitly selected issue.'}\n",
        "```text\n```": "```text\n" + "\n".join(commands) + "\n```",
        "## Test Results\n": f"## Test Results\n\n{test_results} Validation logs are preserved under `.codex-agent/issue-{issue.number}/` in the runner worktree.\n",
        "## Checks That Could Not Run\n": f"## Checks That Could Not Run\n\nLocal verification status: `{validation.status}`. GitHub-hosted CI remains authoritative for pinned Godot provisioning.\n",
        "## Risks and Decisions\n": f"## Risks and Decisions\n\nThe worker uses the existing host Codex installation with model `{model}` and reasoning `{reasoning}`. It never merges the PR.\n",
        "## Deferred Work\n": "## Deferred Work\n\nAutomatic CI-failure repair remains #53. Isolated container worker runtime remains #35/#51/#52. Additional runner trust hardening remains #36/#37.\n",
        "## Human Verification Steps\n": "\n## Human Verification Steps\n\nConfirm the GitHub-hosted `verify` check on this PR before merging.\n",
    }
    for old, new in replacements.items():
        body = body.replace(old, new)
    if "## Acceptance-Criteria Mapping\n\n- [ ]" in body:
        body = body.replace(
            "## Acceptance-Criteria Mapping\n\n- [ ]",
            "## Acceptance-Criteria Mapping\n\n"
            "- [ ] Map the selected issue acceptance criteria during review; worker mechanics are recorded below as execution evidence, not issue acceptance evidence.\n\n"
            "## Codex Invocation Evidence\n\n```text\n"
            f"{command_line(codex_result.command.args)}\n"
            "```\n\n"
            "## Codex Result\n\n```json\n"
            f"{json.dumps({'status': codex_result.result.get('status'), 'blockers': blockers}, indent=2, sort_keys=True)}\n"
            "```",
        )
    return body


def open_or_update_pr(
    issue: Issue,
    layout: Layout,
    repo: str,
    body: str,
    env: dict[str, str],
    command_runner: Callable[..., CommandResult],
) -> str:
    gh = env.get("CODEX_AGENT_GH_BIN", "gh")
    list_result = require_success(
        command_runner(
            [
                gh,
                "pr",
                "list",
                "--repo",
                repo,
                "--head",
                layout.branch,
                "--base",
                layout.base_branch,
                "--state",
                "open",
                "--json",
                "number,url",
            ]
        ),
        "infrastructure_failed",
        EXIT_INFRASTRUCTURE_FAILED,
        "checking existing pull requests",
    )
    try:
        prs = json.loads(list_result.stdout)
    except json.JSONDecodeError as exc:
        raise WorkerError(
            "infrastructure_failed", f"gh returned invalid PR JSON: {exc}", EXIT_INFRASTRUCTURE_FAILED
        ) from exc
    body_path = layout.result_dir / "pr-body.md"
    body_path.write_text(body, encoding="utf-8")
    if len(prs) > 1:
        raise WorkerError(
            "infrastructure_failed", f"multiple open PRs found for {layout.branch}", EXIT_INFRASTRUCTURE_FAILED
        )
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


def dry_run_payload(
    issue: Issue, layout: Layout, repo: str, env: dict[str, str], command_runner: Callable[..., CommandResult]
) -> dict[str, Any]:
    existing_worktrees = worktrees(layout.repo_root, env, command_runner)
    gh = env.get("CODEX_AGENT_GH_BIN", "gh")
    pr_result = command_runner(
        [
            gh,
            "pr",
            "list",
            "--repo",
            repo,
            "--head",
            layout.branch,
            "--base",
            layout.base_branch,
            "--state",
            "open",
            "--json",
            "number,url",
        ]
    )
    existing_prs: list[Any] = []
    if pr_result.returncode == 0 and pr_result.stdout.strip():
        existing_prs = json.loads(pr_result.stdout)
    return {
        "status": "dry_run",
        "issue": {"number": issue.number, "title": issue.title, "url": issue.url, "state": issue.state},
        "branch": layout.branch,
        "branch_exists": branch_exists(layout.repo_root, layout.branch, env, command_runner),
        "worktree": str(layout.worktree),
        "worktree_reused": layout.branch in existing_worktrees,
        "codex_command": command_line(codex_command(layout.result_dir / "codex-initial.txt", env)),
        "validation_command": command_line(validation_command(env)),
        "max_repair_attempts": int(env.get("CODEX_AGENT_MAX_REPAIR_ATTEMPTS", str(DEFAULT_MAX_REPAIR_ATTEMPTS))),
        "existing_prs": existing_prs,
        "repo": repo,
    }


def write_result(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))


def command_line(command: Iterable[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def main(
    argv: list[str] | None = None,
    command_runner: Callable[..., CommandResult] = run_command,
    env: dict[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Run host Codex against one explicit GitHub issue.")
    parser.add_argument("issue_number", nargs="?")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="resolve metadata and planned commands without invoking Codex or mutating GitHub",
    )
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
        layout = Layout(
            layout.repo_root,
            layout.worktree_root,
            layout.branch,
            actual_worktree,
            layout.base_branch,
            layout.result_dir,
            layout.result_path,
        )
        agents_text = (layout.worktree / "AGENTS.md").read_text(encoding="utf-8")
        (layout.result_dir / "prompt.md").write_text(
            prompt_for_issue(issue, layout.worktree, agents_text), encoding="utf-8"
        )
        codex_result, validation = bounded_implementation_loop(issue, layout, agents_text, env, command_runner)
        commit_and_push(issue, layout, env, command_runner)
        body = pr_body(issue, codex_result, validation, load_pr_template(layout.worktree), env)
        pr_url = open_or_update_pr(issue, layout, repo, body, env, command_runner)
        write_result(
            layout.result_path,
            {
                "status": "success",
                "issue": issue.number,
                "branch": layout.branch,
                "worktree": str(layout.worktree),
                "pr": pr_url,
                "validation_attempts": len(validation.attempts),
                "validation_status": validation.status,
            },
        )
        return EXIT_SUCCESS
    except WorkerError as exc:
        issue = args.issue_number if args.issue_number else None
        fallback_root = repo_root_from_script()
        result_path = Path(env.get("CODEX_AGENT_RESULT_PATH", fallback_root / ".codex-agent" / "result.json"))
        write_result(result_path, {"status": exc.status, "issue": issue, "message": exc.message})
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
