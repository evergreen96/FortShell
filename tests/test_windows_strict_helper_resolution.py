from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.windows.windows_strict_helper_resolution import (
    WINDOWS_STRICT_HELPER_ENV,
    WINDOWS_STRICT_HELPER_RUST_DEV,
    resolve_windows_strict_helper_command,
)


class WindowsStrictHelperResolutionTests(unittest.TestCase):
    def test_resolve_explicit_command_prefix_from_environment(self) -> None:
        helper_script = Path(__file__).resolve().parents[1] / "backend" / "windows" / "windows_restricted_host_helper_stub.py"

        with patch.dict(os.environ, {WINDOWS_STRICT_HELPER_ENV: f"{sys.executable} {helper_script}"}):
            command = resolve_windows_strict_helper_command()

        self.assertEqual([sys.executable, str(helper_script)], command)

    def test_resolve_path_helper_from_path_lookup(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with patch(
                "backend.windows.windows_strict_helper_resolution.shutil.which",
                side_effect=lambda name: "C:/tools/ai-ide-restricted-host-helper.exe"
                if name == "ai-ide-restricted-host-helper.exe"
                else None,
            ):
                command = resolve_windows_strict_helper_command()

        self.assertEqual(["C:/tools/ai-ide-restricted-host-helper.exe"], command)

    def test_resolve_rust_dev_helper_to_cargo_run_prefix(self) -> None:
        with patch.dict(os.environ, {WINDOWS_STRICT_HELPER_ENV: WINDOWS_STRICT_HELPER_RUST_DEV}):
            with patch(
                "backend.windows.windows_strict_helper_resolution.shutil.which",
                side_effect=lambda name: "C:/Users/test/.cargo/bin/cargo.exe" if name == "cargo" else None,
            ):
                command = resolve_windows_strict_helper_command()

        self.assertIsNotNone(command)
        assert command is not None
        self.assertEqual("C:/Users/test/.cargo/bin/cargo.exe", command[0])
        self.assertEqual(["run", "--quiet", "--manifest-path"], command[1:4])
        self.assertTrue(command[4].endswith("rust\\Cargo.toml") or command[4].endswith("rust/Cargo.toml"))
        self.assertEqual(["-p", "ai-ide-windows-helper", "--"], command[5:8])

    def test_resolve_rust_dev_helper_returns_none_without_cargo(self) -> None:
        with patch.dict(os.environ, {WINDOWS_STRICT_HELPER_ENV: WINDOWS_STRICT_HELPER_RUST_DEV}):
            with patch("backend.windows.windows_strict_helper_resolution.shutil.which", return_value=None):
                command = resolve_windows_strict_helper_command()

        self.assertIsNone(command)
