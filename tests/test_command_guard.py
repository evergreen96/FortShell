from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_ide.command_guard import CommandGuard


class CommandGuardTests(unittest.TestCase):
    def test_projected_mode_blocks_numeric_sequence_reconstructing_host_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            root.mkdir()
            guard = CommandGuard(root)
            path_codes = ",".join(str(ord(char)) for char in str((root / "secrets" / "token.txt").resolve()))

            decision = guard.evaluate("projected", f"codes=[{path_codes}]")

            self.assertFalse(decision.allowed)
            self.assertIn("host project path", decision.reason)

    def test_projected_mode_blocks_chr_sequence_reconstructing_host_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "project"
            root.mkdir()
            guard = CommandGuard(root)
            secret_path = str((root / "secrets" / "token.txt").resolve())
            chr_sequence = "+".join(f"chr({ord(char)})" for char in secret_path)

            decision = guard.evaluate("projected", f"path = {chr_sequence}")

            self.assertFalse(decision.allowed)
            self.assertIn("host project path", decision.reason)

    def test_strict_preview_blocks_python_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            guard = CommandGuard(Path(temp_dir))

            decision = guard.evaluate("strict-preview", 'python -c "print(1)"')

            self.assertFalse(decision.allowed)
            self.assertIn("interpreter", decision.reason)

    def test_strict_preview_blocks_node_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            guard = CommandGuard(Path(temp_dir))

            decision = guard.evaluate("strict-preview", 'node -e "console.log(1)"')

            self.assertFalse(decision.allowed)
            self.assertIn("interpreter", decision.reason)

    def test_strict_mode_without_preview_allows_python_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            guard = CommandGuard(Path(temp_dir))

            decision = guard.evaluate("strict", 'python -c "print(1)"')

            self.assertTrue(decision.allowed)

    def test_strict_preview_allows_simple_non_interpreter_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            guard = CommandGuard(Path(temp_dir))

            decision = guard.evaluate("strict-preview", "git status")

            self.assertTrue(decision.allowed)


if __name__ == "__main__":
    unittest.main()
