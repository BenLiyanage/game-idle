#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

BROAD_SUPPRESSION = re.compile(r"@warning_ignore_(?:start|restore)\b")
SCRIPT_FAILURE_MARKERS = ("SCRIPT ERROR:", "Failed to load script")
CommandRunner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class CheckResult:
    script: Path
    returncode: int
    output: str


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def discover_scripts(project_root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", "*.gd"],
        cwd=project_root,
        check=False,
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"cannot discover repository GDScript files: {details}")
    relative_paths = [Path(raw) for raw in result.stdout.split("\0") if raw]
    return sorted(project_root / path for path in relative_paths if ".godot" not in path.parts)


def broad_suppressions(script: Path) -> list[str]:
    violations = []
    for line_number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), start=1):
        if BROAD_SUPPRESSION.search(line):
            violations.append(
                f"{script}:{line_number}: broad warning suppression regions are not allowed; "
                'use one documented @warning_ignore("warning_name") annotation'
            )
    return violations


def run_command(args: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
    )


def check_script(
    godot_bin: Path,
    project_root: Path,
    script: Path,
    command_runner: CommandRunner = run_command,
) -> CheckResult:
    relative_script = script.relative_to(project_root)
    with tempfile.TemporaryDirectory(prefix="game-idle-gdscript-log-") as temporary:
        result = command_runner(
            [
                str(godot_bin),
                "--headless",
                "--path",
                str(project_root),
                "--script",
                f"res://{relative_script.as_posix()}",
                "--check-only",
                "--log-file",
                str(Path(temporary) / "godot.log"),
            ],
            project_root,
        )
    output = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
    returncode = result.returncode
    if returncode == 0 and any(marker in output for marker in SCRIPT_FAILURE_MARKERS):
        returncode = 1
    return CheckResult(script=relative_script, returncode=returncode, output=output)


def check_project(
    godot_bin: Path,
    project_root: Path,
    scripts: list[Path] | None = None,
    command_runner: CommandRunner = run_command,
) -> tuple[list[CheckResult], list[str]]:
    scripts = discover_scripts(project_root) if scripts is None else scripts
    suppression_errors = [error for script in scripts for error in broad_suppressions(script)]
    results = [check_script(godot_bin, project_root, script, command_runner) for script in scripts]
    return results, suppression_errors


def report_failures(results: list[CheckResult], suppression_errors: list[str]) -> bool:
    for error in suppression_errors:
        print(error, file=sys.stderr)
    failed_results = [result for result in results if result.returncode != 0]
    for result in failed_results:
        print(f"GDScript policy failed: {result.script}", file=sys.stderr)
        if result.output:
            print(result.output, file=sys.stderr)
    return bool(suppression_errors or failed_results)


def run_fixture_test(godot_bin: Path, repo_root: Path) -> int:
    fixture_root = repo_root / "tests" / "fixtures" / "gdscript"
    fixtures = {
        "valid_typed.gd": fixture_root / "valid_typed.gd.fixture",
        "invalid_untyped.gd": fixture_root / "invalid_untyped.gd.fixture",
        "invalid_warning.gd": fixture_root / "invalid_warning.gd.fixture",
    }
    with tempfile.TemporaryDirectory(prefix="game-idle-gdscript-policy-") as temporary:
        project_root = Path(temporary)
        shutil.copy2(repo_root / "project.godot", project_root / "project.godot")
        for name, source in fixtures.items():
            shutil.copy2(source, project_root / name)

        valid = check_script(godot_bin, project_root, project_root / "valid_typed.gd")
        invalid = check_script(godot_bin, project_root, project_root / "invalid_untyped.gd")
        invalid_warning = check_script(godot_bin, project_root, project_root / "invalid_warning.gd")

    if valid.returncode != 0:
        print("typed GDScript fixture was unexpectedly rejected", file=sys.stderr)
        print(valid.output, file=sys.stderr)
        return 1
    if invalid.returncode == 0:
        print("untyped GDScript fixture was unexpectedly accepted", file=sys.stderr)
        return 1
    if "untyped" not in invalid.output.lower():
        print("untyped GDScript fixture failed without the expected diagnostic", file=sys.stderr)
        print(invalid.output, file=sys.stderr)
        return 1
    if invalid_warning.returncode == 0:
        print("disallowed-warning GDScript fixture was unexpectedly accepted", file=sys.stderr)
        return 1
    if "never used" not in invalid_warning.output.lower():
        print("disallowed-warning GDScript fixture failed without the expected diagnostic", file=sys.stderr)
        print(invalid_warning.output, file=sys.stderr)
        return 1
    print("GDScript policy fixture test ok (typed accepted, untyped and disallowed warning rejected)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check repository-owned GDScript with pinned Godot warnings.")
    parser.add_argument("--godot-bin", required=True, type=Path)
    parser.add_argument("--self-test", action="store_true", help="run isolated positive and negative policy fixtures")
    args = parser.parse_args(argv)
    repo_root = repo_root_from_script()
    godot_bin = args.godot_bin.resolve()
    if not godot_bin.is_file():
        parser.error(f"Godot executable does not exist: {godot_bin}")
    if args.self_test:
        return run_fixture_test(godot_bin, repo_root)

    results, suppression_errors = check_project(godot_bin, repo_root)
    if report_failures(results, suppression_errors):
        return 1
    print(f"GDScript policy ok ({len(results)} scripts)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
