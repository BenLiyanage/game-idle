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


def github_runner_status(config: RunnerConfig) -> int:
    if shutil.which("gh") is None:
        print("GitHub runner availability was not checked because gh is unavailable.")
        return 0
    repo_slug = config.repo_url.removeprefix("https://github.com/")
    jq = f'.runners[] | select(.name == "{config.runner_name}") | {{name, status, busy, labels: [.labels[].name]}}'
    completed = subprocess.run(["gh", "api", f"repos/{repo_slug}/actions/runners", "--jq", jq], text=True, check=False)
    return completed.returncode


def control_runner(command: str, env: dict[str, str]) -> int:
    config = resolve_config(env)
    service_script = validate_runner_identity(config)
    if command in {"start", "stop"}:
        return run_service(service_script, command, config.runner_dir)
    service_status = run_service(service_script, "status", config.runner_dir)
    github_status = github_runner_status(config)
    return service_status or github_status


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
