from __future__ import annotations

import contextlib
import importlib.util
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "ci" / "bootstrap.py"
SPEC = importlib.util.spec_from_file_location("bootstrap", MODULE_PATH)
bootstrap = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["bootstrap"] = bootstrap
SPEC.loader.exec_module(bootstrap)


class BootstrapTests(unittest.TestCase):
    def test_repository_pins_are_the_only_version_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".godot-version").write_text("4.7.2-stable\n", encoding="utf-8")
            (root / "requirements-ruff.txt").write_text("ruff==0.16.6\n", encoding="utf-8")
            pins = bootstrap.read_pins(root)
        self.assertEqual(pins, bootstrap.Pins(godot="4.7.2-stable", ruff="0.16.6"))

    def test_supported_platform_assets_are_derived_from_godot_pin(self) -> None:
        linux = bootstrap.platform_spec("Linux", "x86_64", "4.7.2-stable")
        mac = bootstrap.platform_spec("Darwin", "arm64", "4.7.2-stable")
        self.assertEqual(linux.asset_name, "Godot_v4.7.2-stable_linux.x86_64.zip")
        self.assertEqual(linux.executable_path, Path("Godot_v4.7.2-stable_linux.x86_64"))
        self.assertEqual(mac.asset_name, "Godot_v4.7.2-stable_macos.universal.zip")
        self.assertEqual(mac.executable_path, Path("Godot.app/Contents/MacOS/Godot"))
        with self.assertRaises(bootstrap.BootstrapError):
            bootstrap.platform_spec("Windows", "AMD64", "4.7.2-stable")

    def test_godot_install_is_idempotent_without_a_second_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tools = Path(tmp) / "tools"
            source = Path(tmp) / "godot.zip"
            spec = bootstrap.PlatformSpec("godot.zip", Path("Godot"))
            script = "#!/bin/sh\necho 4.7.2.stable.test\n"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("Godot", script)
            downloads = []

            def local_download(url: str, destination: Path) -> None:
                downloads.append(url)
                shutil.copyfile(source, destination)

            with contextlib.redirect_stderr(io.StringIO()):
                first = bootstrap.ensure_godot(tools, "4.7.2-stable", spec, local_download)
                second = bootstrap.ensure_godot(tools, "4.7.2-stable", spec, local_download)
            self.assertEqual(first, second)
            self.assertEqual(len(downloads), 1)
            self.assertEqual(bootstrap.executable_version(second), "4.7.2.stable.test")
            self.assertEqual((tools / "bin" / "godot").resolve(), second.resolve())

    def test_git_common_tool_root_is_shared_with_issue_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            worktree = Path(tmp) / "issue-worktree"
            root.mkdir()
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Bootstrap Test"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "bootstrap@example.invalid"], cwd=root, check=True)
            (root / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
            subprocess.run(["git", "commit", "--quiet", "-m", "test fixture"], cwd=root, check=True)
            subprocess.run(
                ["git", "worktree", "add", "--quiet", "-b", "agent/issue-17", str(worktree)],
                cwd=root,
                check=True,
            )
            primary_tools = bootstrap.tool_root(root, {})
            issue_tools = bootstrap.tool_root(worktree, {})
        self.assertEqual(primary_tools, issue_tools)
        self.assertTrue(str(primary_tools).endswith(".git/game-idle-tools"))

    def test_tool_root_override_is_explicit_and_absolute(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = root / "shared"
            actual = bootstrap.tool_root(root, {"GAME_IDLE_TOOL_ROOT": str(expected)})
        self.assertEqual(actual, expected.resolve())


if __name__ == "__main__":
    unittest.main()
