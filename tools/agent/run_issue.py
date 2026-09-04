#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
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
DEFAULT_WORKER_IMAGE = "game-idle-codex-worker:local"
DEFAULT_WORKER_CPUS = "2"
DEFAULT_WORKER_MEMORY = "4g"
DEFAULT_WORKER_PIDS = "256"
DEFAULT_WORKER_UID = 1000
DEFAULT_WORKER_GID = 1000
DEFAULT_NETWORK_MODE = "none"
ARTIFACT_SCHEMA_VERSION = 1
DEFAULT_FINALIZER_BIN = "tools/agent/finalize_issue.py"


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
class IsolationConfig:
    image: str
    cpus: str
    memory: str
    pids_limit: str
    network: str
    allow_bridge_network_when_explicit: bool
    uid: int
    gid: int
    readonly_rootfs: bool
    no_new_privileges: bool
    cap_drop: str
    security_opt: str
    tmpfs: tuple[str, ...]


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


def run_identity(issue_number: int, base_sha: str, env: dict[str, str]) -> str:
    if env.get("CODEX_AGENT_RUN_ID"):
        value = env["CODEX_AGENT_RUN_ID"]
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
            raise WorkerError("usage_error", "CODEX_AGENT_RUN_ID contains unsupported characters", EXIT_USAGE)
        return value
    digest = hashlib.sha256(f"{issue_number}:{base_sha}".encode("utf-8")).hexdigest()[:16]
    return f"issue-{issue_number}-{digest}"


def build_layout(repo_root: Path, issue_number: int, env: dict[str, str]) -> Layout:
    branch = branch_for_issue(issue_number, env)
    safe_branch = branch.replace("/", "-")
    worktree_root = Path(env.get("CODEX_AGENT_WORKTREE_ROOT", repo_root / ".worktrees")).expanduser()
    worktree = worktree_root / safe_branch
    base_branch = env.get("CODEX_AGENT_BASE_BRANCH", DEFAULT_BASE_BRANCH)
    result_dir = Path(env.get("CODEX_AGENT_RESULT_DIR", repo_root / ".codex-agent" / f"issue-{issue_number}")).expanduser()
    result_path = Path(env.get("CODEX_AGENT_RESULT_PATH", result_dir / "result.json")).expanduser()
    return Layout(repo_root, worktree_root, branch, worktree, base_branch, result_dir, result_path)


def load_isolation_config(repo_root: Path, env: dict[str, str]) -> IsolationConfig:
    config_path = Path(env.get("CODEX_AGENT_ISOLATION_CONFIG", repo_root / "tools" / "agent" / "isolation.json"))
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise WorkerError("infrastructure_failed", f"missing isolation config: {config_path}: {exc}", EXIT_INFRASTRUCTURE_FAILED)
    except json.JSONDecodeError as exc:
        raise WorkerError("infrastructure_failed", f"invalid isolation config JSON: {exc}", EXIT_INFRASTRUCTURE_FAILED)

    docker = raw.get("docker")
    if not isinstance(docker, dict):
        raise WorkerError("infrastructure_failed", "isolation config must contain a docker object", EXIT_INFRASTRUCTURE_FAILED)

    requested_network = str(env.get("CODEX_AGENT_WORKER_NETWORK", docker.get("network", DEFAULT_NETWORK_MODE)))
    default_network = str(docker.get("network", DEFAULT_NETWORK_MODE))
    explicit_network = "CODEX_AGENT_WORKER_NETWORK" in env
    if explicit_network and requested_network == "bridge" and env.get("CODEX_AGENT_ALLOW_WORKER_NETWORK") != "1":
        raise WorkerError("infrastructure_failed", "bridge networking requires CODEX_AGENT_ALLOW_WORKER_NETWORK=1", EXIT_INFRASTRUCTURE_FAILED)

    config = IsolationConfig(
        image=str(env.get("CODEX_AGENT_WORKER_IMAGE", docker.get("image", DEFAULT_WORKER_IMAGE))),
        cpus=str(env.get("CODEX_AGENT_WORKER_CPUS", docker.get("cpus", DEFAULT_WORKER_CPUS))),
        memory=str(env.get("CODEX_AGENT_WORKER_MEMORY", docker.get("memory", DEFAULT_WORKER_MEMORY))),
        pids_limit=str(env.get("CODEX_AGENT_WORKER_PIDS", docker.get("pids_limit", DEFAULT_WORKER_PIDS))),
        network=requested_network,
        allow_bridge_network_when_explicit=bool(docker.get("allow_bridge_network_when_explicit", False)),
        uid=int(env.get("CODEX_AGENT_WORKER_UID", docker.get("uid", DEFAULT_WORKER_UID))),
        gid=int(env.get("CODEX_AGENT_WORKER_GID", docker.get("gid", DEFAULT_WORKER_GID))),
        readonly_rootfs=bool(docker.get("readonly_rootfs", True)),
        no_new_privileges=bool(docker.get("no_new_privileges", True)),
        cap_drop=str(docker.get("cap_drop", "ALL")),
        security_opt=str(docker.get("security_opt", "no-new-privileges:true")),
        tmpfs=tuple(str(item) for item in docker.get("tmpfs", ["/tmp:rw,nosuid,nodev,size=512m", "/run:rw,nosuid,nodev,size=64m"])),
    )
    validate_isolation_config(config, default_network, explicit_network)
    return config


def validate_isolation_config(config: IsolationConfig, default_network: str | None = None, explicit_network: bool = False) -> None:
    problems: list[str] = []
    if not config.image:
        problems.append("worker image is required")
    if default_network is not None and default_network != "none":
        problems.append("repository default network must be 'none'")
    if config.network != "none":
        allowed_bridge = explicit_network and config.network == "bridge" and config.allow_bridge_network_when_explicit
        if not allowed_bridge:
            problems.append("network must be 'none' unless bridge is explicitly enabled for Codex API access")
    if config.uid == 0 or config.gid == 0:
        problems.append("worker user and group must be non-root")
    if not config.readonly_rootfs:
        problems.append("read-only root filesystem is required")
    if not config.no_new_privileges:
        problems.append("no-new-privileges is required")
    if config.security_opt != "no-new-privileges:true":
        problems.append("security_opt must be no-new-privileges:true")
    if config.cap_drop != "ALL":
        problems.append("all Linux capabilities must be dropped")
    if not config.cpus or not config.memory or not config.pids_limit:
        problems.append("CPU, memory, and PID limits are required")
    if problems:
        raise WorkerError("infrastructure_failed", "isolation config failed closed: " + "; ".join(problems), EXIT_INFRASTRUCTURE_FAILED)


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


def codex_command(prompt_path: Path, schema_path: Path, output_path: Path, env: dict[str, str]) -> list[str]:
    command = [
        env.get("CODEX_AGENT_CODEX_BIN", "codex"),
        "exec",
        "--cd",
        "/workspace",
        "--sandbox",
        env.get("CODEX_AGENT_SANDBOX", DEFAULT_SANDBOX),
        "--ask-for-approval",
        env.get("CODEX_AGENT_APPROVAL_POLICY", DEFAULT_APPROVAL_POLICY),
        "--ephemeral",
        "--ignore-rules",
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


def copy_minimal_codex_home(env: dict[str, str]) -> tuple[tempfile.TemporaryDirectory[str] | None, Path | None, list[str]]:
    if env.get("CODEX_AGENT_SKIP_CODEX_AUTH") == "1":
        return None, None, []

    source = Path(env.get("CODEX_AGENT_CODEX_HOME_SOURCE", Path.home() / ".codex")).expanduser()
    auth = source / "auth.json"
    if not auth.exists():
        raise WorkerError("infrastructure_failed", f"Codex auth file is required but missing: {auth}", EXIT_INFRASTRUCTURE_FAILED)

    temp_home = tempfile.TemporaryDirectory(prefix="codex-agent-home-")
    target = Path(temp_home.name)
    shutil.copy2(auth, target / "auth.json")
    exposed = [str(auth)]
    config = source / "config.toml"
    if config.exists():
        shutil.copy2(config, target / "config.toml")
        exposed.append(str(config))
    for child in target.iterdir():
        child.chmod(0o400)
    return temp_home, target, exposed


def worker_payload_script(codex_args: list[str], run_verify_after_codex: bool) -> str:
    codex_json = json.dumps(codex_args)
    verify_json = json.dumps(["bash", "tools/ci/verify.sh"])
    return f"""#!/usr/bin/env python3
import json
import subprocess
import sys
from pathlib import Path

def run(args, stdin_path=None):
    stdin = None
    if stdin_path:
        stdin = open(stdin_path, "r", encoding="utf-8")
    try:
        completed = subprocess.run(args, cwd="/workspace", stdin=stdin, text=True, check=False)
    finally:
        if stdin:
            stdin.close()
    return completed.returncode

codex = json.loads({codex_json!r})
prompt_path = "/results/prompt.md"
code = run(codex, prompt_path)
if code != 0:
    sys.exit(code)
if {str(run_verify_after_codex)}:
    sys.exit(run(json.loads({verify_json!r})))
"""


def docker_run_command(
    config: IsolationConfig,
    layout: Layout,
    container_name: str,
    codex_home: Path | None,
    command: list[str],
    env: dict[str, str],
) -> list[str]:
    validate_worker_paths(layout, codex_home)
    args = [
        env.get("CODEX_AGENT_DOCKER_BIN", "docker"),
        "run",
        "--rm",
        "--name",
        container_name,
        "--network",
        config.network,
        "--cpus",
        config.cpus,
        "--memory",
        config.memory,
        "--pids-limit",
        config.pids_limit,
        "--read-only",
        "--user",
        f"{config.uid}:{config.gid}",
        "--cap-drop",
        config.cap_drop,
        "--security-opt",
        config.security_opt,
    ]
    for tmpfs in config.tmpfs:
        args.extend(["--tmpfs", tmpfs])
    args.extend(["--mount", f"type=bind,src={layout.worktree},dst=/workspace,rw"])
    args.extend(["--mount", f"type=bind,src={layout.result_dir},dst=/results,rw"])
    if codex_home is not None:
        args.extend(["--mount", f"type=bind,src={codex_home},dst=/codex-home,ro"])
        args.extend(["--env", "CODEX_HOME=/codex-home"])
    args.extend(["--env", "HOME=/tmp/codex-home"])
    if env.get("GODOT_BIN"):
        args.extend(["--env", f"GODOT_BIN={env['GODOT_BIN']}"])
    args.append(config.image)
    args.extend(command)
    enforce_docker_invariants(args, layout, codex_home)
    return args


def validate_worker_paths(layout: Layout, codex_home: Path | None) -> None:
    worktree = layout.worktree.resolve()
    result_dir = layout.result_dir.resolve()
    repo_root = layout.repo_root.resolve()
    if not str(worktree).startswith(str(layout.worktree_root.resolve())):
        raise WorkerError("infrastructure_failed", f"worker worktree is outside configured worktree root: {worktree}", EXIT_INFRASTRUCTURE_FAILED)
    if result_dir == repo_root or repo_root in result_dir.parents:
        pass
    elif not str(result_dir).startswith(str(Path(tempfile.gettempdir()).resolve())):
        raise WorkerError("infrastructure_failed", f"result directory must be repository-local or temporary: {result_dir}", EXIT_INFRASTRUCTURE_FAILED)
    forbidden = [
        Path.home(),
        Path.home() / ".ssh",
        Path.home() / ".git-credentials",
        Path.home() / ".config" / "gh",
        Path("/var/run/docker.sock"),
        Path("/run/docker.sock"),
    ]
    mounted = [worktree, result_dir]
    if codex_home is not None:
        mounted.append(codex_home.resolve())
    for path in mounted:
        for forbidden_path in forbidden:
            resolved_forbidden = forbidden_path.resolve()
            if path == resolved_forbidden:
                raise WorkerError("infrastructure_failed", f"forbidden host path would be mounted: {path}", EXIT_INFRASTRUCTURE_FAILED)
            if resolved_forbidden != Path.home().resolve() and resolved_forbidden in path.parents:
                raise WorkerError("infrastructure_failed", f"forbidden host path would be mounted: {path}", EXIT_INFRASTRUCTURE_FAILED)


def enforce_docker_invariants(args: list[str], layout: Layout, codex_home: Path | None) -> None:
    rendered = "\n".join(args)
    forbidden_tokens = {"--privileged", "--pid=host", "--network=host", "--cap-add", "/var/run/docker.sock", "/run/docker.sock"}
    for token in forbidden_tokens:
        if token in args or token in rendered:
            raise WorkerError("infrastructure_failed", f"forbidden Docker option or mount requested: {token}", EXIT_INFRASTRUCTURE_FAILED)
    required = [
        "--rm",
        "--network",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--cpus",
        "--memory",
        "--pids-limit",
    ]
    missing = [item for item in required if item not in args]
    if missing:
        raise WorkerError("infrastructure_failed", "Docker command missing required isolation options: " + ", ".join(missing), EXIT_INFRASTRUCTURE_FAILED)
    network_index = args.index("--network") + 1
    if args[network_index] not in {"none", "bridge"}:
        raise WorkerError("infrastructure_failed", f"unexpected Docker network mode: {args[network_index]}", EXIT_INFRASTRUCTURE_FAILED)
    writable_mounts = [arg for index, arg in enumerate(args) if index > 0 and args[index - 1] == "--mount" and arg.endswith(",rw")]
    expected_writable = {
        f"type=bind,src={layout.worktree},dst=/workspace,rw",
        f"type=bind,src={layout.result_dir},dst=/results,rw",
    }
    if set(writable_mounts) != expected_writable:
        raise WorkerError("infrastructure_failed", f"unexpected writable host mounts: {writable_mounts}", EXIT_INFRASTRUCTURE_FAILED)
    if codex_home is not None and f"type=bind,src={codex_home},dst=/codex-home,ro" not in args:
        raise WorkerError("infrastructure_failed", "Codex credential home must be mounted read-only", EXIT_INFRASTRUCTURE_FAILED)


def run_isolated_worker(
    layout: Layout,
    config: IsolationConfig,
    codex_home: Path | None,
    command: list[str],
    env: dict[str, str],
    command_runner: Callable[..., CommandResult],
) -> CommandResult:
    container_name = f"codex-issue-{layout.branch.replace('/', '-')}-{uuid.uuid4().hex[:12]}"
    docker_args = docker_run_command(config, layout, container_name, codex_home, command, env)
    return command_runner(docker_args, cwd=layout.repo_root)


def run_implementation_and_verification(
    layout: Layout,
    config: IsolationConfig,
    env: dict[str, str],
    command_runner: Callable[..., CommandResult],
) -> tuple[dict[str, Any], CommandResult, list[str]]:
    prompt_path = layout.result_dir / "prompt.md"
    schema_path = layout.result_dir / "codex-result.schema.json"
    output_path = layout.result_dir / "codex-result.json"
    codex_args = codex_command(Path("/results/prompt.md"), Path("/results/codex-result.schema.json"), Path("/results/codex-result.json"), env)
    payload_path = layout.result_dir / "worker_payload.py"
    payload_path.write_text(worker_payload_script(codex_args, True), encoding="utf-8")
    temp_home, codex_home, exposed = copy_minimal_codex_home(env)
    try:
        result = run_isolated_worker(layout, config, codex_home, ["python3", "/results/worker_payload.py"], env, command_runner)
    finally:
        if temp_home is not None:
            temp_home.cleanup()
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        if output_path.exists():
            parsed = read_codex_result(output_path)
            if parsed["status"] == "blocked":
                raise WorkerError("blocked", parsed.get("summary", "Codex reported blocked"), EXIT_BLOCKED)
            if parsed["status"] == "success":
                raise WorkerError("verification_failed", "repository verification failed inside isolated worker", EXIT_VERIFICATION_FAILED)
        raise WorkerError("implementation_failed", message, EXIT_IMPLEMENTATION_FAILED)
    parsed = read_codex_result(output_path)
    if parsed["status"] == "blocked":
        raise WorkerError("blocked", parsed.get("summary", "Codex reported blocked"), EXIT_BLOCKED)
    if parsed["status"] == "failure":
        raise WorkerError("implementation_failed", parsed.get("summary", "Codex reported failure"), EXIT_IMPLEMENTATION_FAILED)
    return parsed, result, exposed


def run_verification(worktree: Path, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> CommandResult:
    result = command_runner(["bash", "tools/ci/verify.sh"], cwd=worktree)
    if result.returncode != 0:
        raise WorkerError("verification_failed", "repository verification failed", EXIT_VERIFICATION_FAILED)
    return result


def git_output(args: list[str], cwd: Path, env: dict[str, str], command_runner: Callable[..., CommandResult], action: str) -> str:
    git = env.get("CODEX_AGENT_GIT_BIN", "git")
    result = require_success(
        command_runner([git, *args], cwd=cwd),
        "infrastructure_failed",
        EXIT_INFRASTRUCTURE_FAILED,
        action,
    )
    return result.stdout


def base_sha(layout: Layout, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> str:
    return git_output(["rev-parse", f"origin/{layout.base_branch}^{{commit}}"], layout.repo_root, env, command_runner, "resolving base SHA").strip()


def changed_paths(worktree: Path, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> list[str]:
    output = git_output(["diff", "--name-only", "--no-ext-diff"], worktree, env, command_runner, "listing changed paths")
    return [line for line in output.splitlines() if line]


def change_patch(worktree: Path, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> str:
    return git_output(["diff", "--binary", "--no-ext-diff"], worktree, env, command_runner, "creating change patch")


def validate_changed_paths(paths: list[str]) -> None:
    for path in paths:
        if path.startswith("/") or path.startswith("../") or "/../" in path or path == "..":
            raise WorkerError("implementation_failed", f"unsafe changed path in artifact: {path}", EXIT_IMPLEMENTATION_FAILED)
        if path.startswith(".git/") or path == ".git":
            raise WorkerError("implementation_failed", f"unsafe git metadata path in artifact: {path}", EXIT_IMPLEMENTATION_FAILED)


def write_change_artifact(
    issue: Issue,
    layout: Layout,
    repo: str,
    resolved_base_sha: str,
    parsed_codex_result: dict[str, Any],
    worker_result: CommandResult,
    evidence: dict[str, Any],
    env: dict[str, str],
    command_runner: Callable[..., CommandResult],
) -> dict[str, Any]:
    paths = changed_paths(layout.worktree, env, command_runner)
    validate_changed_paths(paths)
    patch_text = change_patch(layout.worktree, env, command_runner)
    if not patch_text.strip():
        raise WorkerError("implementation_failed", "Codex completed but produced no committable changes", EXIT_IMPLEMENTATION_FAILED)
    artifact = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "repository": repo,
        "issue": {"number": issue.number, "title": issue.title, "url": issue.url},
        "base": {"branch": layout.base_branch, "sha": resolved_base_sha},
        "branch": layout.branch,
        "run": {"id": run_identity(issue.number, resolved_base_sha, env)},
        "status": "success",
        "change": {"format": "git-diff-binary", "changed_paths": paths, "patch": patch_text},
        "verification": {
            "trusted_finalizer_reran": False,
            "command": "bash tools/ci/verify.sh",
            "environment": "disposable untrusted worker",
            "worker_exit_code": worker_result.returncode,
            "codex_result": parsed_codex_result,
        },
        "provenance": {
            "worker_contract": "tools/agent/run_issue.sh",
            "result_artifacts": str(layout.result_dir),
        },
        "publisher": {"ran": False, "required": False},
    }
    artifact_path = layout.result_dir / "change-artifact.json"
    write_result(artifact_path, artifact)
    return artifact


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
        command_runner([git, "commit", "-m", f"Implement issue #{issue.number} disposable worker isolation"], cwd=layout.worktree),
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


def pr_body(issue: Issue, codex_result: dict[str, Any], worker_result: CommandResult, template: str, evidence: dict[str, Any]) -> str:
    body = template
    summary = codex_result.get("summary", "").strip()
    controls = json.dumps(evidence, indent=2, sort_keys=True)
    replacements = {
        "Closes #<issue>": f"Closes #{issue.number}",
        "## Scope Summary\n": f"## Scope Summary\n\n{summary}\n",
        "```text\n```": "```text\ntools/agent/run_issue.sh " + str(issue.number) + "\npython3 -m unittest tests.agent.test_run_issue\npython3 tests/agent/security_probe.py\nbash tools/ci/verify.sh\n```",
        "## Test Results\n": f"## Test Results\n\nWorker container exited {worker_result.returncode}. Canonical verification was invoked inside the isolated worker; see `.codex-agent/issue-{issue.number}/worker-output.txt` and local/CI logs for the exact Godot result.\n",
        "## Checks That Could Not Run\n": "## Checks That Could Not Run\n\nIf local Docker, Codex, or pinned Godot is unavailable, the failing command output is preserved as the real result instead of being bypassed.\n",
        "## Risks and Decisions\n": "## Risks and Decisions\n\nThe host launcher remains trusted and performs GitHub publishing. GitHub write credential separation is deferred to #36; runner trust redesign is deferred to #37.\n",
        "## Deferred Work\n": "## Deferred Work\n\nGitHub repository-write credential separation remains #36. GitHub Actions runner trust remains #37. Godot provisioning remains #33.\n",
        "## Human Verification Steps\n": "## Human Verification Steps\n\nReview the security evidence block below, the negative security test logs, and CI result before merging.\n",
    }
    for old, new in replacements.items():
        body = body.replace(old, new)
    if "## Acceptance-Criteria Mapping\n\n- [ ]" in body:
        body = body.replace(
            "## Acceptance-Criteria Mapping\n\n- [ ]",
            "## Acceptance-Criteria Mapping\n\n- [x] Trusted host launcher creates a fresh disposable worker container per issue run.\n- [x] Project/model-controlled implementation and canonical verification run inside the locked-down worker.\n- [x] Negative security probe attempts prohibited host access and asserts failure.\n- [x] Deliberate result artifacts survive worker teardown.\n\n## Isolation Evidence\n\n```json\n" + controls + "\n```",
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


def evidence_payload(config: IsolationConfig, layout: Layout, docker_args: list[str], exposed_codex_files: list[str]) -> dict[str, Any]:
    return {
        "container_invocation": command_line(docker_args),
        "host_paths_mounted": [
            {"path": str(layout.worktree), "container_path": "/workspace", "mode": "rw", "reason": "intended issue worktree"},
            {"path": str(layout.result_dir), "container_path": "/results", "mode": "rw", "reason": "deliberate result/evidence artifacts"},
            {"path": "ephemeral minimal Codex home", "container_path": "/codex-home", "mode": "ro", "reason": "Codex auth/config only"},
        ],
        "writable_paths": ["/workspace", "/results", "/tmp tmpfs", "/run tmpfs"],
        "user": f"{config.uid}:{config.gid}",
        "capabilities": "ALL dropped",
        "seccomp": "Docker default seccomp retained",
        "no_new_privileges": config.no_new_privileges,
        "readonly_rootfs": config.readonly_rootfs,
        "cpu_limit": config.cpus,
        "memory_limit": config.memory,
        "pid_limit": config.pids_limit,
        "docker_socket": "not mounted",
        "network": config.network,
        "codex_credential_material": exposed_codex_files,
        "residual_risks": [
            "Codex auth/config are readable by the Codex process and technically by project-controlled subprocesses inside the worker.",
            "Destination-level network allow-listing is deferred; issue #35 fails closed to network none for project/model-controlled execution.",
            "GitHub repository-write credential separation is deferred to #36.",
            "Runner trust redesign is deferred to #37.",
        ],
    }


def dry_run_payload(issue: Issue, layout: Layout, repo: str, env: dict[str, str], command_runner: Callable[..., CommandResult]) -> dict[str, Any]:
    existing_worktrees = worktrees(layout.repo_root, env, command_runner)
    gh = env.get("CODEX_AGENT_GH_BIN", "gh")
    pr_result = command_runner(
        [gh, "pr", "list", "--repo", repo, "--head", layout.branch, "--base", layout.base_branch, "--state", "open", "--json", "number,url"]
    )
    existing_prs: list[Any] = []
    if pr_result.returncode == 0 and pr_result.stdout.strip():
        existing_prs = json.loads(pr_result.stdout)
    config = load_isolation_config(layout.repo_root, env)
    prompt_path = Path("/results/prompt.md")
    schema_path = Path("/results/codex-result.schema.json")
    output_path = Path("/results/codex-result.json")
    container_name = f"codex-issue-{layout.branch.replace('/', '-')}-DRYRUN"
    docker_args = docker_run_command(config, layout, container_name, Path("/tmp/codex-agent-home-dry-run"), ["python3", "/results/worker_payload.py"], env)
    return {
        "status": "dry_run",
        "issue": {"number": issue.number, "title": issue.title, "url": issue.url, "state": issue.state},
        "branch": layout.branch,
        "branch_exists": branch_exists(layout.repo_root, layout.branch, env, command_runner),
        "worktree": str(layout.worktree),
        "worktree_reused": layout.branch in existing_worktrees,
        "verification_command": "bash tools/ci/verify.sh inside worker container",
        "codex_command": command_line(codex_command(prompt_path, schema_path, output_path, env)),
        "docker_command": command_line(docker_args),
        "existing_prs": existing_prs,
        "repo": repo,
    }


def main(argv: list[str] | None = None, command_runner: Callable[..., CommandResult] = run_command, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Codex against one explicit GitHub issue in a disposable worker container.")
    parser.add_argument("issue_number", nargs="?")
    parser.add_argument("--dry-run", action="store_true", help="resolve metadata and planned commands without invoking Codex or mutating GitHub")
    parser.add_argument("--publish-local", action="store_true", help="supervised compatibility path: commit, push, and create/update a PR from this trusted host")
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
            payload["publish_local"] = args.publish_local
            write_result(layout.result_path, payload)
            return EXIT_SUCCESS

        layout.result_dir.mkdir(parents=True, exist_ok=True)
        config = load_isolation_config(repo_root, env)
        actual_worktree = ensure_worktree(layout, env, command_runner)
        layout = Layout(layout.repo_root, layout.worktree_root, layout.branch, actual_worktree, layout.base_branch, layout.result_dir, layout.result_path)
        resolved_base_sha = base_sha(layout, env, command_runner)
        agents_text = (layout.worktree / "AGENTS.md").read_text(encoding="utf-8")
        prompt_path = layout.result_dir / "prompt.md"
        schema_path = layout.result_dir / "codex-result.schema.json"
        prompt = prompt_for_issue(issue, layout.worktree, agents_text)
        prompt_path.write_text(prompt, encoding="utf-8")
        write_schema(schema_path)
        parsed_codex_result, worker_result, exposed = run_implementation_and_verification(layout, config, env, command_runner)
        (layout.result_dir / "worker-output.txt").write_text(worker_result.stdout + worker_result.stderr, encoding="utf-8")
        dry_docker_args = docker_run_command(config, layout, f"codex-issue-{issue.number}-evidence", Path("/tmp/codex-agent-home-evidence"), ["python3", "/results/worker_payload.py"], env)
        evidence = evidence_payload(config, layout, dry_docker_args, exposed)
        artifact = write_change_artifact(issue, layout, repo, resolved_base_sha, parsed_codex_result, worker_result, evidence, env, command_runner)
        pr_url = None
        if args.publish_local:
            commit_and_push(issue, layout, env, command_runner)
            body = pr_body(issue, parsed_codex_result, worker_result, load_pr_template(layout.worktree), evidence)
            pr_url = open_or_update_pr(issue, layout, repo, body, env, command_runner)
            artifact["publisher"] = {"ran": True, "required": False, "mode": "supervised_local", "pr": pr_url}
            write_result(layout.result_dir / "change-artifact.json", artifact)
        write_result(
            layout.result_path,
            {
                "status": "success",
                "issue": issue.number,
                "branch": layout.branch,
                "base_sha": resolved_base_sha,
                "worktree": str(layout.worktree),
                "artifact": str(layout.result_dir / "change-artifact.json"),
                "pr": pr_url,
                "publisher_ran": args.publish_local,
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
