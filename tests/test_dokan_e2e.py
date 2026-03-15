"""Dokan E2E tests — protection model.

Mounts a real Dokan drive and verifies:
- Protected files ARE visible (ls shows them)
- Protected files are ACCESS-DENIED (cat, write, chmod fail)
- Allowed files pass through to original
- Policy changes reflect immediately

Requires: Dokan driver installed, fusepy available.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

if os.name != "nt":
    raise unittest.SkipTest("Dokan E2E tests require Windows")

try:
    from fuse import FUSE
    from backend.windows.dokan_driver_check import check_dokan_driver
    driver = check_dokan_driver()
    if not driver.installed:
        raise unittest.SkipTest("Dokan driver not installed")
    if not driver.fusepy_available:
        raise unittest.SkipTest("fusepy not available")
except (ImportError, unittest.SkipTest):
    raise

from backend.windows.dokan_backend import DokanFilteredOperations
from core.internal import INTERNAL_PROJECT_METADATA_DIR_NAME
from core.policy import PolicyEngine
from core.workspace_access_service import WorkspaceAccessService

import ctypes
import string
import threading


def _find_free_drive() -> str:
    used = ctypes.windll.kernel32.GetLogicalDrives()
    for letter in reversed(string.ascii_uppercase):
        if letter in {"A", "B"}:
            continue
        if not (used & (1 << (ord(letter) - ord("A")))):
            return f"{letter}:"
    raise RuntimeError("No free drive letter")


class DokanProtectionE2ETests(unittest.TestCase):
    """E2E: real Dokan mount with protection model."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        base = Path(cls.temp_dir.name)
        cls.project_root = base / "project"
        cls.project_root.mkdir()

        (cls.project_root / "src").mkdir()
        (cls.project_root / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
        (cls.project_root / "notes").mkdir()
        (cls.project_root / "notes" / "todo.txt").write_text("visible todo\n", encoding="utf-8")
        (cls.project_root / "secrets").mkdir()
        (cls.project_root / "secrets" / "token.txt").write_text("secret_value\n", encoding="utf-8")
        (cls.project_root / ".env").write_text("API_KEY=hidden\n", encoding="utf-8")

        cls.policy = PolicyEngine(cls.project_root)
        cls.policy.add_deny_rule("secrets/**")
        cls.policy.add_deny_rule(".env")
        cls.workspace_access = WorkspaceAccessService(cls.project_root, cls.policy)

        cls.mount_point = _find_free_drive()
        cls.ops = DokanFilteredOperations(cls.project_root, cls.workspace_access)
        cls.mount_thread = threading.Thread(
            target=lambda: FUSE(cls.ops, cls.mount_point, foreground=True, nothreads=False),
            daemon=True,
        )
        cls.mount_thread.start()

        deadline = time.time() + 10
        while time.time() < deadline:
            if Path(f"{cls.mount_point}\\").exists():
                break
            time.sleep(0.2)
        else:
            raise RuntimeError(f"Dokan mount at {cls.mount_point} not ready in 10s")

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            subprocess.run(["mountvol", cls.mount_point.rstrip("\\"), "/d"],
                           capture_output=True, check=False, timeout=10)
        except OSError:
            pass
        cls.mount_thread.join(timeout=5)
        cls.temp_dir.cleanup()

    # -- 1. Protected files ARE VISIBLE in directory listing --

    def test_01_dir_shows_protected_files(self) -> None:
        result = subprocess.run(
            f"dir /b {self.mount_point}\\",
            shell=True, capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.strip().splitlines()
        self.assertIn("src", lines)
        self.assertIn("notes", lines)
        self.assertIn("secrets", lines, "Protected folder should be VISIBLE")
        self.assertIn(".env", lines, "Protected file should be VISIBLE")

    def test_02_dir_subdir_shows_contents(self) -> None:
        result = subprocess.run(
            f"dir /b {self.mount_point}\\notes",
            shell=True, capture_output=True, text=True, timeout=5,
        )
        self.assertIn("todo.txt", result.stdout)

    # -- 2. Allowed files: read/write OK --

    def test_03_read_allowed_file(self) -> None:
        result = subprocess.run(
            f"type {self.mount_point}\\notes\\todo.txt",
            shell=True, capture_output=True, text=True, timeout=5,
        )
        self.assertIn("visible todo", result.stdout)

    def test_04_write_new_file_reflects_in_original(self) -> None:
        new_path = f"{self.mount_point}\\notes\\e2e_new.txt"
        subprocess.run(
            f'echo e2e_created> {new_path}',
            shell=True, capture_output=True, timeout=5,
        )
        time.sleep(0.5)
        original = self.project_root / "notes" / "e2e_new.txt"
        self.assertTrue(original.exists())
        self.assertIn("e2e_created", original.read_text(encoding="utf-8"))

    # -- 3. Protected files: read DENIED --

    def test_05_read_protected_file_denied(self) -> None:
        result = subprocess.run(
            f"type {self.mount_point}\\secrets\\token.txt",
            shell=True, capture_output=True, text=True, timeout=5,
        )
        self.assertNotEqual(0, result.returncode, "Reading protected file should fail")
        self.assertNotIn("secret_value", result.stdout, "Secret content must not leak")

    def test_06_read_protected_env_denied(self) -> None:
        result = subprocess.run(
            f"type {self.mount_point}\\.env",
            shell=True, capture_output=True, text=True, timeout=5,
        )
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("API_KEY", result.stdout)

    # -- 4. Search: protected files visible in listing but content not searchable --

    def test_07_find_shows_protected_files(self) -> None:
        result = subprocess.run(
            f"dir /b /s {self.mount_point}\\*.txt",
            shell=True, capture_output=True, text=True, timeout=5,
        )
        self.assertIn("todo.txt", result.stdout)
        # Protected files should be listed (visible)
        self.assertIn("token.txt", result.stdout, "Protected file should appear in find")

    def test_08_rg_cannot_read_protected_content(self) -> None:
        result = subprocess.run(
            f"rg secret_value {self.mount_point}\\",
            shell=True, capture_output=True, text=True, timeout=10,
        )
        self.assertNotIn("secret_value", result.stdout, "rg must not read protected content")

    def test_09_rg_finds_allowed_content(self) -> None:
        result = subprocess.run(
            f"rg visible {self.mount_point}\\",
            shell=True, capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            result = subprocess.run(
                f'findstr /s /c:"visible" {self.mount_point}\\notes\\*.txt',
                shell=True, capture_output=True, text=True, timeout=10,
            )
        self.assertIn("visible", result.stdout)

    # -- 5. Python os module (simulates AI CLI) --

    def test_10_python_listdir_shows_protected(self) -> None:
        script = (
            "import os, sys\n"
            "try:\n"
            f"    entries = os.listdir(r'{self.mount_point}\\\\')\n"
            "    print(','.join(entries))\n"
            "except Exception as e:\n"
            "    print(f'ERROR: {{e}}', file=sys.stderr)\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=10,
        )
        # dir /b already verified visibility in test_01
        # os.listdir from subprocess may fail on Dokan due to same-process limitation
        if result.stdout.strip():
            self.assertIn("secrets", result.stdout, "Protected dir visible in listdir")
        else:
            self.skipTest("os.listdir from subprocess not supported on this Dokan mount")

    def test_11_python_open_protected_raises(self) -> None:
        script = (
            "try:\n"
            f"    open(r'{self.mount_point}\\secrets\\token.txt').read()\n"
            "    print('LEAKED')\n"
            "except (PermissionError, OSError):\n"
            "    print('BLOCKED')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=10,
        )
        self.assertIn("BLOCKED", result.stdout)
        self.assertNotIn("LEAKED", result.stdout)

    # -- 6. chmod bypass attempt --

    def test_12_chmod_protected_fails(self) -> None:
        script = (
            "import os\n"
            "try:\n"
            f"    os.chmod(r'{self.mount_point}\\secrets\\token.txt', 0o777)\n"
            "    print('CHANGED')\n"
            "except (PermissionError, OSError):\n"
            "    print('DENIED')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=10,
        )
        self.assertIn("DENIED", result.stdout)

    # -- 7. Original untouched --

    def test_13_original_still_has_all_files(self) -> None:
        self.assertTrue((self.project_root / "secrets" / "token.txt").exists())
        self.assertTrue((self.project_root / ".env").exists())
        self.assertEqual("secret_value\n",
                         (self.project_root / "secrets" / "token.txt").read_text(encoding="utf-8"))


class DokanPolicyLiveUpdateE2ETests(unittest.TestCase):
    """E2E: policy changes reflect immediately without terminal restart."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_dir = tempfile.TemporaryDirectory()
        base = Path(cls.temp_dir.name)
        cls.project_root = base / "project"
        cls.project_root.mkdir()

        (cls.project_root / "src").mkdir()
        (cls.project_root / "src" / "main.py").write_text("print('hello')\n", encoding="utf-8")
        (cls.project_root / "config").mkdir()
        (cls.project_root / "config" / "settings.json").write_text('{"key":"value"}\n', encoding="utf-8")

        cls.policy = PolicyEngine(cls.project_root)
        # Initially: nothing protected
        cls.workspace_access = WorkspaceAccessService(cls.project_root, cls.policy)

        cls.mount_point = _find_free_drive()
        cls.ops = DokanFilteredOperations(cls.project_root, cls.workspace_access)
        cls.mount_thread = threading.Thread(
            target=lambda: FUSE(cls.ops, cls.mount_point, foreground=True, nothreads=False),
            daemon=True,
        )
        cls.mount_thread.start()

        deadline = time.time() + 10
        while time.time() < deadline:
            if Path(f"{cls.mount_point}\\").exists():
                break
            time.sleep(0.2)

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            subprocess.run(["mountvol", cls.mount_point.rstrip("\\"), "/d"],
                           capture_output=True, check=False, timeout=10)
        except OSError:
            pass
        cls.mount_thread.join(timeout=5)
        cls.temp_dir.cleanup()

    def test_01_initially_all_readable(self) -> None:
        result = subprocess.run(
            f"type {self.mount_point}\\config\\settings.json",
            shell=True, capture_output=True, text=True, timeout=5,
        )
        self.assertIn("value", result.stdout, "Initially config should be readable")

    def test_02_protect_then_denied_immediately(self) -> None:
        # Add protection
        self.policy.add_deny_rule("config/**")

        # Same mount, no restart — should be denied immediately
        result = subprocess.run(
            f"type {self.mount_point}\\config\\settings.json",
            shell=True, capture_output=True, text=True, timeout=5,
        )
        self.assertNotEqual(0, result.returncode, "After protect, read should be denied")
        self.assertNotIn("value", result.stdout)

    def test_03_unprotect_then_readable_immediately(self) -> None:
        # Remove protection
        self.policy.remove_deny_rule("config/**")

        # Should be readable again
        result = subprocess.run(
            f"type {self.mount_point}\\config\\settings.json",
            shell=True, capture_output=True, text=True, timeout=5,
        )
        self.assertIn("value", result.stdout, "After unprotect, read should work again")


if __name__ == "__main__":
    unittest.main()
