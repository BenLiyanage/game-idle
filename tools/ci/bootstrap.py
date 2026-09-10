#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

GODOT_RELEASE_BASE_URL = "https://github.com/godotengine/godot-builds/releases/download"


class BootstrapError(Exception):
    pass


@dataclass(frozen=True)
class Pins:
    godot: str
    ruff: str


@dataclass(frozen=True)
class PlatformSpec:
    asset_name: str
    executable_path: Path


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parents[2]


def run_checked(args: Sequence[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(args),
            cwd=str(cwd) if cwd else None,
            check=True,
            text=True,
            capture_output=True,
        )
    except OSError as exc:
        raise BootstrapError(f"cannot run {args[0]}: {exc}") from exc
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "").strip()
        raise BootstrapError(f"command failed ({' '.join(args)}): {details}") from exc


def read_pins(repo_root: Path) -> Pins:
    godot = (repo_root / ".godot-version").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+-stable", godot):
        raise BootstrapError(f"unsupported Godot pin in .godot-version: {godot!r}")

    requirements = (repo_root / "requirements-ruff.txt").read_text(encoding="utf-8").splitlines()
    matches = []
    for line in requirements:
        match = re.fullmatch(r"\s*ruff==([^\s#]+)\s*(?:#.*)?", line)
        if match:
            matches.append(match.group(1))
    if len(matches) != 1:
        raise BootstrapError("requirements-ruff.txt must contain exactly one ruff==<version> pin")
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+", matches[0]):
        raise BootstrapError(f"unsupported Ruff pin in requirements-ruff.txt: {matches[0]!r}")
    return Pins(godot=godot, ruff=matches[0])


def git_common_dir(repo_root: Path) -> Path:
    result = run_checked(["git", "rev-parse", "--git-common-dir"], cwd=repo_root)
    common_dir = Path(result.stdout.strip())
    if not common_dir.is_absolute():
        common_dir = repo_root / common_dir
    return common_dir.resolve()


def tool_root(repo_root: Path, environ: dict[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    override = environ.get("GAME_IDLE_TOOL_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return git_common_dir(repo_root) / "game-idle-tools"


def platform_spec(system: str, machine: str, godot_version: str) -> PlatformSpec:
    if system == "Linux" and machine == "x86_64":
        asset = f"Godot_v{godot_version}_linux.x86_64.zip"
        return PlatformSpec(asset, Path(asset.removesuffix(".zip")))
    if system == "Darwin" and machine in {"arm64", "x86_64"}:
        asset = f"Godot_v{godot_version}_macos.universal.zip"
        return PlatformSpec(asset, Path("Godot.app/Contents/MacOS/Godot"))
    raise BootstrapError(
        f"unsupported bootstrap platform: {system} {machine}; supported environments are "
        "GitHub-hosted Linux x86_64 and macOS arm64/x86_64"
    )


def executable_version(executable: Path) -> str | None:
    if not executable.is_file() or not os.access(executable, os.X_OK):
        return None
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or result.stderr).strip()


def godot_version_matches(actual: str | None, pin: str) -> bool:
    expected = pin.replace("-stable", ".stable")
    return bool(actual and (actual.startswith(expected) or actual.startswith(pin)))


def default_download(url: str, destination: Path) -> None:
    run_checked(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--retry",
            "3",
            "--output",
            str(destination),
            url,
        ]
    )


def safe_extract(archive: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            target = (destination / member.filename).resolve()
            if os.path.commonpath([destination, target]) != str(destination):
                raise BootstrapError(f"Godot archive contains an unsafe path: {member.filename}")
        zipped.extractall(destination)


def replace_symlink(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.parent / f".{link.name}.tmp-{os.getpid()}"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    os.replace(temporary, link)


def ensure_godot(
    tools: Path,
    pin: str,
    spec: PlatformSpec,
    downloader: Callable[[str, Path], None] = default_download,
) -> Path:
    install_dir = tools / "godot" / pin
    executable = install_dir / spec.executable_path
    if not godot_version_matches(executable_version(executable), pin):
        install_dir.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="godot-download-", dir=tools) as temporary_raw:
            temporary = Path(temporary_raw)
            archive = temporary / spec.asset_name
            extracted = temporary / "extracted"
            extracted.mkdir()
            url = f"{GODOT_RELEASE_BASE_URL}/{pin}/{spec.asset_name}"
            print(f"Downloading pinned Godot from {url}", file=sys.stderr)
            downloader(url, archive)
            try:
                safe_extract(archive, extracted)
            except (OSError, zipfile.BadZipFile) as exc:
                raise BootstrapError(f"cannot extract {spec.asset_name}: {exc}") from exc
            extracted_executable = extracted / spec.executable_path
            if not extracted_executable.is_file():
                raise BootstrapError(f"Godot executable was not found in {spec.asset_name}: {spec.executable_path}")
            extracted_executable.chmod(extracted_executable.stat().st_mode | 0o755)
            actual = executable_version(extracted_executable)
            if not godot_version_matches(actual, pin):
                raise BootstrapError(f"expected Godot {pin}, downloaded executable reported {actual!r}")
            shutil.rmtree(install_dir, ignore_errors=True)
            shutil.move(str(extracted), str(install_dir))
    replace_symlink(tools / "bin" / "godot", executable)
    return executable


def ensure_ruff(repo_root: Path, tools: Path, pin: str) -> Path:
    install_dir = tools / "ruff" / pin
    python = install_dir / "bin" / "python"
    ruff = install_dir / "bin" / "ruff"
    if executable_version(ruff) != f"ruff {pin}":
        shutil.rmtree(install_dir, ignore_errors=True)
        install_dir.parent.mkdir(parents=True, exist_ok=True)
        print(f"Creating pinned Ruff environment for {pin}", file=sys.stderr)
        run_checked([sys.executable, "-m", "venv", str(install_dir)])
        run_checked(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-input",
                "--requirement",
                str(repo_root / "requirements-ruff.txt"),
            ]
        )
        actual = executable_version(ruff)
        if actual != f"ruff {pin}":
            raise BootstrapError(f"expected Ruff {pin}, installed executable reported {actual!r}")
    replace_symlink(tools / "bin" / "ruff", ruff)
    return ruff


def environment_payload(tools: Path, godot: Path, ruff: Path, pins: Pins) -> dict[str, str]:
    return {
        "bin_dir": str((tools / "bin").resolve()),
        "godot_bin": str(godot.resolve()),
        "godot_version": pins.godot,
        "ruff_bin": str(ruff.resolve()),
        "ruff_version": pins.ruff,
        "tool_root": str(tools.resolve()),
    }


def bootstrap(repo_root: Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    pins = read_pins(repo_root)
    tools = tool_root(repo_root, environ)
    tools.mkdir(parents=True, exist_ok=True)
    spec = platform_spec(platform.system(), platform.machine(), pins.godot)
    godot = ensure_godot(tools, pins.godot, spec)
    ruff = ensure_ruff(repo_root, tools, pins.ruff)
    return environment_payload(tools, godot, ruff, pins)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare the repository-pinned verification toolchain.")
    parser.add_argument(
        "--print-tool-root",
        action="store_true",
        help="print the deterministic shared tool root without preparing it",
    )
    args = parser.parse_args(argv)
    repo_root = repo_root_from_script()
    try:
        if args.print_tool_root:
            print(tool_root(repo_root))
            return 0
        print(json.dumps(bootstrap(repo_root), sort_keys=True))
        return 0
    except (BootstrapError, OSError) as exc:
        print(f"bootstrap failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
