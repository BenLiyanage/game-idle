from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "ci" / "check_gdscript.py"
SPEC = importlib.util.spec_from_file_location("check_gdscript", MODULE_PATH)
check_gdscript = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules["check_gdscript"] = check_gdscript
SPEC.loader.exec_module(check_gdscript)


class CheckGDScriptTests(unittest.TestCase):
    def test_discovers_scripts_but_not_import_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "scripts" / "main.gd"
            imported = root / ".godot" / "generated.gd"
            ignored = root / ".worktrees" / "other" / "unrelated.gd"
            subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
            (root / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
            source.parent.mkdir()
            imported.parent.mkdir()
            ignored.parent.mkdir(parents=True)
            source.write_text("extends Node\n", encoding="utf-8")
            imported.write_text("invalid fixture\n", encoding="utf-8")
            ignored.write_text("invalid fixture\n", encoding="utf-8")
            scripts = check_gdscript.discover_scripts(root)
        self.assertEqual(scripts, [source])

    def test_rejects_broad_suppression_regions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "broad.gd"
            script.write_text('@warning_ignore_start("unused_parameter")\nextends Node\n', encoding="utf-8")
            errors = check_gdscript.broad_suppressions(script)
        self.assertEqual(len(errors), 1)
        self.assertIn("broad warning suppression regions are not allowed", errors[0])

    def test_allows_one_targeted_suppression(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "targeted.gd"
            script.write_text('@warning_ignore("unused_parameter")\nextends Node\n', encoding="utf-8")
            errors = check_gdscript.broad_suppressions(script)
        self.assertEqual(errors, [])

    def test_invokes_godot_check_only_for_project_resource(self) -> None:
        calls: list[tuple[list[str], Path]] = []

        def fake_runner(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            calls.append((args, cwd))
            return subprocess.CompletedProcess(args, 0, "parse ok", "")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "nested" / "typed.gd"
            script.parent.mkdir()
            script.write_text("extends Node\n", encoding="utf-8")
            result = check_gdscript.check_script(Path("/tools/godot"), root, script, fake_runner)

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.script, Path("nested/typed.gd"))
        self.assertEqual(calls[0][1], root)
        self.assertIn("res://nested/typed.gd", calls[0][0])
        self.assertIn("--check-only", calls[0][0])

    def test_script_error_is_failure_even_when_godot_returns_zero(self) -> None:
        def fake_runner(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            del cwd
            return subprocess.CompletedProcess(args, 0, "SCRIPT ERROR: Parse Error: Variable has no static type.", "")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = root / "untyped.gd"
            script.write_text("extends Node\n", encoding="utf-8")
            result = check_gdscript.check_script(Path("/tools/godot"), root, script, fake_runner)

        self.assertEqual(result.returncode, 1)
        self.assertIn("SCRIPT ERROR", result.output)


if __name__ == "__main__":
    unittest.main()
