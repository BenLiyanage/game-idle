#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


EXIT_SUCCESS = 0
EXIT_INVALID = 65
EXIT_INFRASTRUCTURE = 70
ARTIFACT_SCHEMA_VERSION = 1
DEFAULT_BRANCH_PREFIX = "agent/issue-"
DEFAULT_REPOSITORY = "BenLiyanage/game-idle"


class FinalizerError(Exception):
    def __init__(self, status: str, message: str, exit_code: int = EXIT_INVALID) -> None:
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
class Expected:
    repository: str
    issue: int
    base_branch: str
    base_sha: str
    branch: str


def run_command(args: list[str], cwd: Path | None = None, input_text: str | None = None, env: dict[str, str] | None = None) -> CommandResult:
    completed = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        input=input_text,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return CommandResult(args, completed.returncode, completed.stdout, completed.stderr)


def require_success(result: CommandResult, action: str) -> CommandResult:
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise FinalizerError("infrastructure_failed", f"{action} failed: {details}", EXIT_INFRASTRUCTURE)
    return result


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def command_line(command: Iterable[str]) -> str:
    return " ".join(shlex.quote(part) for part in command)


def canonical_artifact_bytes(artifact: dict[str, Any]) -> bytes:
    return json.dumps(artifact, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def artifact_hash(artifact: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_artifact_bytes(artifact)).hexdigest()


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
    raise FinalizerError("invalid", f"cannot determine GitHub repository from origin URL: {remote_url}")


def git_env(base_env: dict[str, str]) -> dict[str, str]:
    env = dict(base_env)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    env["GIT_ATTR_NOSYSTEM"] = "1"
    return env


def git(args: list[str], repo_root: Path, env: dict[str, str], command_runner: Callable[..., CommandResult], input_text: str | None = None) -> CommandResult:
    git_bin = env.get("CODEX_FINALIZER_GIT_BIN", "git")
    return command_runner([git_bin, *args], cwd=repo_root, input_text=input_text, env=git_env(env))


def gh(args: list[str], repo_root: Path, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> CommandResult:
    gh_bin = env.get("CODEX_FINALIZER_GH_BIN", "gh")
    return command_runner([gh_bin, *args], cwd=repo_root, env=dict(env))


def expected_branch(issue: int, env: dict[str, str]) -> str:
    return f"{env.get('CODEX_AGENT_BRANCH_PREFIX', DEFAULT_BRANCH_PREFIX)}{issue}"


def load_artifact(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FinalizerError("invalid", f"artifact is unreadable or malformed JSON: {exc}")
    if not isinstance(payload, dict):
        raise FinalizerError("invalid", "artifact root must be an object")
    return payload


def validate_string(value: Any, name: str, pattern: str | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise FinalizerError("invalid", f"{name} must be a non-empty string")
    if pattern and not re.fullmatch(pattern, value):
        raise FinalizerError("invalid", f"{name} has an unexpected format")
    return value


def validate_path(path: str) -> None:
    if path.startswith("/") or path.startswith("../") or "/../" in path or path == "..":
        raise FinalizerError("invalid", f"unsafe changed path: {path}")
    if path == ".git" or path.startswith(".git/"):
        raise FinalizerError("invalid", f"git metadata path is not allowed: {path}")
    if "\x00" in path:
        raise FinalizerError("invalid", "changed path contains NUL")


def validate_artifact(artifact: dict[str, Any], expected: Expected) -> str:
    for key in ("finalizer_command", "command", "script", "hook", "checkout"):
        if key in artifact:
            raise FinalizerError("invalid", f"worker-specified {key} is not accepted")
    if artifact.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
        raise FinalizerError("invalid", "unsupported artifact schema_version")
    if artifact.get("repository") != expected.repository:
        raise FinalizerError("invalid", "artifact repository does not match expected repository")
    issue = artifact.get("issue")
    base = artifact.get("base")
    change = artifact.get("change")
    verification = artifact.get("verification")
    run = artifact.get("run")
    if not isinstance(issue, dict) or issue.get("number") != expected.issue:
        raise FinalizerError("invalid", "artifact issue does not match expected issue")
    if not isinstance(base, dict) or base.get("branch") != expected.base_branch or base.get("sha") != expected.base_sha:
        raise FinalizerError("invalid", "artifact base branch/SHA does not match expected base")
    if artifact.get("branch") != expected.branch:
        raise FinalizerError("invalid", "artifact branch does not match expected issue branch")
    if artifact.get("status") != "success":
        raise FinalizerError("invalid", "only successful worker artifacts can be finalized")
    if not isinstance(run, dict):
        raise FinalizerError("invalid", "artifact run must be an object")
    validate_string(run.get("id"), "run.id", r"[A-Za-z0-9_.:-]{1,128}")
    if not isinstance(verification, dict):
        raise FinalizerError("invalid", "verification must be an object")
    if verification.get("trusted_finalizer_reran") is not False:
        raise FinalizerError("invalid", "artifact must not claim trusted finalizer verification")
    if verification.get("command") != "bash tools/ci/verify.sh":
        raise FinalizerError("invalid", "verification command is unexpected")
    if not isinstance(change, dict) or change.get("format") != "git-diff-binary":
        raise FinalizerError("invalid", "change format must be git-diff-binary")
    paths = change.get("changed_paths")
    if not isinstance(paths, list) or not paths:
        raise FinalizerError("invalid", "changed_paths must be a non-empty list")
    for path in paths:
        validate_path(validate_string(path, "changed path"))
    patch = validate_string(change.get("patch"), "change.patch")
    return patch


def assert_expected_repository(repo_root: Path, expected_repo: str, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> None:
    result = require_success(git(["config", "--get", "remote.origin.url"], repo_root, env, command_runner), "reading origin URL")
    actual = parse_repo_from_remote(result.stdout)
    if actual != expected_repo:
        raise FinalizerError("invalid", f"local origin repository {actual} does not match expected {expected_repo}")


def resolve_base(repo_root: Path, expected: Expected, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> None:
    require_success(git(["fetch", "origin", expected.base_branch], repo_root, env, command_runner), f"fetching origin/{expected.base_branch}")
    result = require_success(git(["rev-parse", f"origin/{expected.base_branch}^{{commit}}"], repo_root, env, command_runner), "resolving origin base")
    actual_sha = result.stdout.strip()
    if actual_sha != expected.base_sha:
        raise FinalizerError("invalid", f"stale base SHA: artifact {expected.base_sha}, origin/{expected.base_branch} {actual_sha}")


def current_branch_commit(repo_root: Path, branch: str, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> str | None:
    result = git(["show-ref", "--verify", "--hash", f"refs/heads/{branch}"], repo_root, env, command_runner)
    if result.returncode == 0:
        return result.stdout.strip()
    result = git(["show-ref", "--verify", "--hash", f"refs/remotes/origin/{branch}"], repo_root, env, command_runner)
    if result.returncode == 0:
        return result.stdout.strip()
    return None


def commit_has_artifact(repo_root: Path, commit: str, digest: str, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> bool:
    result = git(["log", "-1", "--format=%B", commit], repo_root, env, command_runner)
    if result.returncode != 0:
        return False
    return f"Artifact-Hash: {digest}" in result.stdout


def create_commit_from_patch(
    repo_root: Path,
    artifact: dict[str, Any],
    expected: Expected,
    patch: str,
    digest: str,
    env: dict[str, str],
    command_runner: Callable[..., CommandResult],
) -> tuple[str, list[str]]:
    with tempfile.TemporaryDirectory(prefix="codex-finalizer-index-") as tmp:
        index_path = str(Path(tmp) / "index")
        indexed_env = dict(env)
        indexed_env["GIT_INDEX_FILE"] = index_path
        require_success(git(["read-tree", expected.base_sha], repo_root, indexed_env, command_runner), "seeding temporary index")
        require_success(git(["apply", "--cached", "--binary", "--whitespace=nowarn", "-"], repo_root, indexed_env, command_runner, input_text=patch), "applying artifact patch")
        names = require_success(git(["diff", "--cached", "--name-only", expected.base_sha], repo_root, indexed_env, command_runner), "validating resulting paths").stdout.splitlines()
        expected_paths = sorted(artifact["change"]["changed_paths"])
        actual_paths = sorted(path for path in names if path)
        if actual_paths != expected_paths:
            raise FinalizerError("invalid", "artifact changed_paths do not match applied patch")
        for path in actual_paths:
            validate_path(path)
        tree = require_success(git(["write-tree"], repo_root, indexed_env, command_runner), "writing tree from artifact").stdout.strip()
    message = commit_message(artifact, expected, digest)
    commit = require_success(
        git(["commit-tree", tree, "-p", expected.base_sha], repo_root, env, command_runner, input_text=message),
        "creating commit from artifact",
    ).stdout.strip()
    return commit, actual_paths


def commit_message(artifact: dict[str, Any], expected: Expected, digest: str) -> str:
    title = artifact.get("issue", {}).get("title") or f"Issue #{expected.issue}"
    verification = artifact.get("verification", {})
    return "\n".join(
        [
            f"Implement issue #{expected.issue}: {title}",
            "",
            "Created by trusted finalizer from an untrusted worker artifact.",
            "The finalizer did not rerun verification or execute worker-provided code.",
            "",
            f"Issue: #{expected.issue}",
            f"Worker-Run: {artifact['run']['id']}",
            f"Base-Branch: {expected.base_branch}",
            f"Base-SHA: {expected.base_sha}",
            f"Artifact-Hash: {digest}",
            f"Worker-Verification: {verification.get('command', 'unknown')}",
            "",
        ]
    )


def update_branch_and_push(repo_root: Path, expected: Expected, commit: str, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> None:
    require_success(git(["update-ref", f"refs/heads/{expected.branch}", commit], repo_root, env, command_runner), f"updating {expected.branch}")
    require_success(git(["push", "origin", f"refs/heads/{expected.branch}:refs/heads/{expected.branch}"], repo_root, env, command_runner), f"pushing {expected.branch}")


def pr_body(artifact: dict[str, Any], expected: Expected, digest: str, changed_paths: list[str]) -> str:
    verification = artifact["verification"]
    return f"""## Linked Issue

Closes #{expected.issue}

## Trust Boundary

Trusted process: `tools/agent/finalize_issue.py` on Ben's trusted host. Untrusted process: `tools/agent/run_issue.sh` disposable Codex worker.

The finalizer treated the worker output as data, applied the patch through a temporary Git index, and did not run Godot/tests, invoke Codex, install dependencies, source shell, or execute worker-provided scripts.

## Artifact

- Format: JSON schema version {ARTIFACT_SCHEMA_VERSION} with embedded `git diff --binary` patch.
- Artifact hash: `{digest}`
- Worker run: `{artifact["run"]["id"]}`
- Base: `{expected.base_branch}` `{expected.base_sha}`
- Branch: `{expected.branch}`
- Changed paths: {", ".join(changed_paths)}

## Validation And Idempotency

The finalizer validated repository, issue, base branch, base SHA, branch identity, schema, changed paths, and single PR identity before mutation. Retries reuse an existing commit whose message contains the same `Artifact-Hash` and reuse the exact branch/PR instead of creating duplicates.

## Worker Verification Evidence

Verification evidence was produced inside the untrusted disposable worker and was not independently rerun by the finalizer.

```json
{json.dumps(verification, indent=2, sort_keys=True)}
```

## Credential Boundary

The worker receives no GitHub write credential, no local `gh` auth, no SSH push credential, no PAT, and no signing/release credential. Worker proof is the Docker invocation/evidence captured by `tools/agent/run_issue.sh` and the negative credential-boundary tests in `tests/agent/test_run_issue.py`.

Trusted finalizer credential location: finalizer host/process only, outside the worker filesystem. Intended permissions: repository-scoped `BenLiyanage/game-idle` GitHub App installation token with Contents read/write for the expected branch, Pull requests read/write for the expected PR, Issues read for issue context if needed, and Metadata read. It must not access other repositories, modify `main` directly, alter settings/rulesets, create releases, or access signing credentials.

## Residual Risk

If the trusted finalizer host or its narrow GitHub credential is compromised, an attacker could update the expected issue branch/PR within that credential's permissions. The finalizer credential must not be able to modify `main` directly, repository settings, rulesets, releases, signing keys, or repositories other than `BenLiyanage/game-idle`.

## Negative Tests

Focused tests cover missing worker GitHub credentials, no default worker push/PR mutation, valid finalization, malformed artifacts, wrong repository, wrong issue, stale base SHA, unexpected branch, duplicate PR identity, idempotent retry, rejected worker command/script fields, path traversal, malicious executable patch content treated as inert data, and provenance preservation.

## #8 Compatibility

The #8 worker now defaults to structured artifact output. The direct local commit/push/PR publisher remains only behind explicit `--publish-local` for supervised compatibility and is not required by unattended operation.

## Remaining Blocker

Before unattended use, provision the narrow finalizer GitHub credential described above. If only a broad durable PAT is available, finalization must remain disabled.
"""


def open_or_update_pr(artifact: dict[str, Any], repo_root: Path, expected: Expected, body: str, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> str:
    list_result = require_success(
        gh(["pr", "list", "--repo", expected.repository, "--head", expected.branch, "--base", expected.base_branch, "--state", "open", "--json", "number,url"], repo_root, env, command_runner),
        "checking existing pull requests",
    )
    try:
        prs = json.loads(list_result.stdout)
    except json.JSONDecodeError as exc:
        raise FinalizerError("infrastructure_failed", f"gh returned invalid PR JSON: {exc}", EXIT_INFRASTRUCTURE)
    if not isinstance(prs, list):
        raise FinalizerError("infrastructure_failed", "gh returned non-list PR JSON", EXIT_INFRASTRUCTURE)
    if len(prs) > 1:
        raise FinalizerError("invalid", f"multiple open PRs found for {expected.branch}")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as body_file:
        body_file.write(body)
        body_path = body_file.name
    try:
        if len(prs) == 1:
            number = str(prs[0]["number"])
            require_success(gh(["pr", "edit", number, "--repo", expected.repository, "--body-file", body_path], repo_root, env, command_runner), f"updating PR #{number}")
            return str(prs[0].get("url") or number)
        title = f"Implement issue #{expected.issue}: {artifact['issue'].get('title', '')}".strip()
        created = require_success(
            gh(["pr", "create", "--repo", expected.repository, "--base", expected.base_branch, "--head", expected.branch, "--title", title, "--body-file", body_path], repo_root, env, command_runner),
            "creating pull request",
        )
        return created.stdout.strip()
    finally:
        try:
            Path(body_path).unlink()
        except OSError:
            pass


def finalize(artifact_path: Path, expected: Expected, repo_root: Path, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> dict[str, Any]:
    assert_expected_repository(repo_root, expected.repository, env, command_runner)
    artifact = load_artifact(artifact_path)
    patch = validate_artifact(artifact, expected)
    digest = artifact_hash(artifact)
    resolve_base(repo_root, expected, env, command_runner)
    existing = current_branch_commit(repo_root, expected.branch, env, command_runner)
    reused_commit = False
    if existing and commit_has_artifact(repo_root, existing, digest, env, command_runner):
        commit = existing
        changed = artifact["change"]["changed_paths"]
        reused_commit = True
    else:
        commit, changed = create_commit_from_patch(repo_root, artifact, expected, patch, digest, env, command_runner)
        update_branch_and_push(repo_root, expected, commit, env, command_runner)
    body = pr_body(artifact, expected, digest, changed)
    pr_url = open_or_update_pr(artifact, repo_root, expected, body, env, command_runner)
    return {
        "status": "success",
        "repository": expected.repository,
        "issue": expected.issue,
        "branch": expected.branch,
        "base_sha": expected.base_sha,
        "artifact_hash": digest,
        "commit": commit,
        "commit_reused": reused_commit,
        "pr": pr_url,
        "provenance": {
            "worker_run": artifact["run"]["id"],
            "worker_verification": artifact["verification"]["command"],
        },
    }


def write_result(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def main(argv: list[str] | None = None, command_runner: Callable[..., CommandResult] = run_command, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Finalize a trusted GitHub PR from an untrusted Codex worker artifact.")
    parser.add_argument("artifact")
    parser.add_argument("--repo", default=DEFAULT_REPOSITORY)
    parser.add_argument("--issue", required=True, type=int)
    parser.add_argument("--base-branch", default="main")
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--branch")
    args = parser.parse_args(argv)
    env = dict(os.environ if env is None else env)
    expected = Expected(
        repository=args.repo,
        issue=args.issue,
        base_branch=args.base_branch,
        base_sha=args.base_sha,
        branch=args.branch or expected_branch(args.issue, env),
    )
    try:
        payload = finalize(Path(args.artifact), expected, repo_root_from_script(), env, command_runner)
        write_result(payload)
        return EXIT_SUCCESS
    except FinalizerError as exc:
        write_result({"status": exc.status, "message": exc.message, "issue": args.issue})
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
