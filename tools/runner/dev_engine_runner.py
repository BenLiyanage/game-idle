#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXIT_INFRASTRUCTURE_FAILED = 40
EXIT_USAGE = 64
DEFAULT_REPO_URL = "https://github.com/BenLiyanage/game-idle"


@dataclass(frozen=True)
class RunnerConfig:
    runner_dir: Path
    runner_name: str
    repo_url: str


@dataclass(frozen=True)
class GitHubRunnerRecord:
    name: str
    status: str
    busy: bool
    labels: tuple[str, ...]


@dataclass(frozen=True)
class LocalServiceStatus:
    exit_code: int
    started: bool


class RunnerControlError(Exception):
    def __init__(self, message: str, exit_code: int = EXIT_INFRASTRUCTURE_FAILED) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


def load_runner_metadata(runner_file: Path) -> dict[str, Any]:
    try:
        payload = json.loads(runner_file.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RunnerControlError(f"cannot read GitHub runner identity metadata: {runner_file}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RunnerControlError(f"GitHub runner identity metadata is not an object: {runner_file}")
    return payload


def resolve_config(env: dict[str, str]) -> RunnerConfig:
    if not env.get("DEV_ENGINE_RUNNER_DIR") or not env.get("DEV_ENGINE_RUNNER_NAME"):
        raise RunnerControlError(
            "usage: DEV_ENGINE_RUNNER_DIR=<path> DEV_ENGINE_RUNNER_NAME=<name> dev_engine_runner.sh start|stop|status",
            EXIT_USAGE,
        )
    runner_dir = Path(env["DEV_ENGINE_RUNNER_DIR"]).expanduser().resolve()
    if not runner_dir.is_dir():
        raise RunnerControlError(f"expected runner directory cannot be resolved: {env['DEV_ENGINE_RUNNER_DIR']}")
    return RunnerConfig(
        runner_dir=runner_dir,
        runner_name=env["DEV_ENGINE_RUNNER_NAME"],
        repo_url=env.get("DEV_ENGINE_RUNNER_REPO_URL", DEFAULT_REPO_URL).rstrip("/"),
    )


def validate_runner_identity(config: RunnerConfig) -> Path:
    runner_file = config.runner_dir / ".runner"
    service_script = config.runner_dir / "svc.sh"
    if not runner_file.is_file() or not service_script.is_file() or not os.access(service_script, os.X_OK):
        raise RunnerControlError(
            f"expected GitHub runner identity or service control script is missing in {config.runner_dir}"
        )
    metadata = load_runner_metadata(runner_file)
    actual_name = str(metadata.get("agentName") or "")
    actual_repo = str(metadata.get("gitHubUrl") or metadata.get("serverUrl") or "").rstrip("/")
    if actual_name != config.runner_name:
        raise RunnerControlError(
            f"runner identity mismatch: expected {config.runner_name!r}, found {actual_name or '<empty>'!r}"
        )
    if actual_repo != config.repo_url:
        raise RunnerControlError(
            f"runner repository mismatch: expected {config.repo_url!r}, found {actual_repo or '<empty>'!r}"
        )
    return service_script


def run_service(service_script: Path, command: str, runner_dir: Path) -> int:
    completed = subprocess.run([str(service_script), command], cwd=runner_dir, text=True, check=False)
    return completed.returncode


def local_service_status(service_script: Path, runner_dir: Path) -> LocalServiceStatus:
    completed = subprocess.run(
        [str(service_script), "status"],
        cwd=runner_dir,
        text=True,
        check=False,
        capture_output=True,
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)
    started = "\nStarted:\n" in f"\n{completed.stdout}" and "\nStopped" not in f"\n{completed.stdout}"
    return LocalServiceStatus(exit_code=completed.returncode, started=started)


def runner_record_from_api_response(payload: str, runner_name: str) -> GitHubRunnerRecord | None:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RunnerControlError(f"cannot parse GitHub runner status response: {exc}") from exc
    runners = data.get("runners") if isinstance(data, dict) else None
    if not isinstance(runners, list):
        raise RunnerControlError("GitHub runner status response did not include a runners list")

    for runner in runners:
        if not isinstance(runner, dict) or runner.get("name") != runner_name:
            continue
        labels = runner.get("labels", [])
        label_names = tuple(
            str(label.get("name")) for label in labels if isinstance(label, dict) and isinstance(label.get("name"), str)
        )
        return GitHubRunnerRecord(
            name=runner_name,
            status=str(runner.get("status") or ""),
            busy=bool(runner.get("busy")),
            labels=label_names,
        )
    return None


def github_runner_status(config: RunnerConfig) -> int:
    if shutil.which("gh") is None:
        print("GitHub runner availability cannot be checked because gh is unavailable.", file=sys.stderr)
        return EXIT_INFRASTRUCTURE_FAILED
    repo_slug = config.repo_url.removeprefix("https://github.com/")
    completed = subprocess.run(
        ["gh", "api", f"repos/{repo_slug}/actions/runners"],
        text=True,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        print(f"GitHub runner availability check failed: {detail}", file=sys.stderr)
        return EXIT_INFRASTRUCTURE_FAILED
    try:
        record = runner_record_from_api_response(completed.stdout, config.runner_name)
    except RunnerControlError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code
    if record is None:
        print(
            f"GitHub runner {config.runner_name!r} was not found in {repo_slug}; "
            "the configured runner is not available.",
            file=sys.stderr,
        )
        return EXIT_INFRASTRUCTURE_FAILED
    labels = ", ".join(record.labels) if record.labels else "<none>"
    if record.status != "online":
        print(
            f"GitHub runner {record.name!r} is {record.status or '<unknown>'} "
            f"(busy={record.busy}, labels={labels}); the configured runner is not available.",
            file=sys.stderr,
        )
        return EXIT_INFRASTRUCTURE_FAILED
    print(f"GitHub runner {record.name!r} is online (busy={record.busy}, labels={labels}).")
    return 0


def control_runner(command: str, env: dict[str, str]) -> int:
    config = resolve_config(env)
    service_script = validate_runner_identity(config)
    if command in {"start", "stop"}:
        return run_service(service_script, command, config.runner_dir)
    service_status = local_service_status(service_script, config.runner_dir)
    github_status = github_runner_status(config)
    if service_status.exit_code != 0:
        return service_status.exit_code
    if not service_status.started:
        print("Local GitHub runner service is not started; the configured runner is not available.", file=sys.stderr)
        return EXIT_INFRASTRUCTURE_FAILED
    return github_status


def main(argv: list[str] | None = None, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Control the known Dev Engine GitHub runner service.")
    parser.add_argument("command", choices=["start", "stop", "status"])
    args = parser.parse_args(argv)
    try:
        return control_runner(args.command, dict(os.environ if env is None else env))
    except RunnerControlError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code


if __name__ == "__main__":
    sys.exit(main())
