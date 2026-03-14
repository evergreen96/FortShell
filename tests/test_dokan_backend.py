"""Contract tests for the Dokan filtered filesystem backend (protection model).

Protected files are VISIBLE but ACCESS-DENIED.
These tests verify the contract that all OS backends must satisfy.
"""

from __future__ import annotations

import errno
import os
import tempfile
import unittest
from pathlib import Path

from fuse import FuseOSError

from backend.windows.dokan_backend import DokanFilteredOperations
from core.internal import INTERNAL_PROJECT_METADATA_DIR_NAME, INTERNAL_RUNTIME_DIR_NAME
from core.policy import PolicyEngine
from core.workspace_access_service import WorkspaceAccessService


class DokanProtectionModelTests(unittest.TestCase):
    """Contract tests: protected files visible but access-denied."""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        base = Path(self.temp_dir.name)
        self.root = base / "project"
        self.root.mkdir()
        (self.root / "src").mkdir()
        (self.root / "secrets").mkdir()
        (self.root / INTERNAL_PROJECT_METADATA_DIR_NAME).mkdir()
        (self.root / INTERNAL_RUNTIME_DIR_NAME).mkdir()
        (self.root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
        (self.root / "secrets" / "token.txt").write_text("hidden\n", encoding="utf-8")
        (self.root / INTERNAL_PROJECT_METADATA_DIR_NAME / "policy.json").write_text("{}", encoding="utf-8")
        self.policy = PolicyEngine(self.root)
        self.policy.add_deny_rule("secrets/**")
        self.workspace_access = WorkspaceAccessService(self.root, self.policy)
        self.ops = DokanFilteredOperations(self.root, self.workspace_access)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    # -- Visibility: protected files ARE listed --

    def test_readdir_includes_protected_entries(self) -> None:
        entries = self.ops.readdir("/", None)

        self.assertIn("src", entries)
        self.assertIn("secrets", entries)
        self.assertIn(INTERNAL_PROJECT_METADATA_DIR_NAME, entries)

    # -- Metadata: protected files show zero permissions --

    def test_getattr_protected_returns_zero_permissions(self) -> None:
        info = self.ops.getattr("/secrets/token.txt")

        self.assertEqual(0, info["st_mode"])

    def test_getattr_allowed_returns_real_permissions(self) -> None:
        info = self.ops.getattr("/src/main.py")

        self.assertNotEqual(0, info["st_mode"])

    # -- Read: denied for protected --

    def test_open_protected_raises_eacces(self) -> None:
        with self.assertRaises(FuseOSError) as ctx:
            self.ops.open("/secrets/token.txt", os.O_RDONLY)
        self.assertEqual(errno.EACCES, ctx.exception.errno)

    def test_open_allowed_succeeds(self) -> None:
        fh = self.ops.open("/src/main.py", os.O_RDONLY)
        self.ops.release("/src/main.py", fh)

    # -- Write: denied for protected --

    def test_create_in_protected_dir_raises_eacces(self) -> None:
        with self.assertRaises(FuseOSError) as ctx:
            self.ops.create("/secrets/new.txt", 0o644)
        self.assertEqual(errno.EACCES, ctx.exception.errno)

    # -- Modify: denied for protected --

    def test_chmod_protected_raises_eacces(self) -> None:
        with self.assertRaises(FuseOSError) as ctx:
            self.ops.chmod("/secrets/token.txt", 0o777)
        self.assertEqual(errno.EACCES, ctx.exception.errno)

    def test_unlink_protected_raises_eacces(self) -> None:
        with self.assertRaises(FuseOSError) as ctx:
            self.ops.unlink("/secrets/token.txt")
        self.assertEqual(errno.EACCES, ctx.exception.errno)

    def test_rename_protected_raises_eacces(self) -> None:
        with self.assertRaises(FuseOSError) as ctx:
            self.ops.rename("/secrets/token.txt", "/secrets/moved.txt")
        self.assertEqual(errno.EACCES, ctx.exception.errno)

    # -- Symlink/hardlink: always denied --

    def test_symlink_raises_eacces(self) -> None:
        with self.assertRaises(FuseOSError) as ctx:
            self.ops.symlink("/secrets/token.txt", "/link.txt")
        self.assertEqual(errno.EACCES, ctx.exception.errno)

    def test_link_raises_eacces(self) -> None:
        with self.assertRaises(FuseOSError) as ctx:
            self.ops.link("/secrets/token.txt", "/link.txt")
        self.assertEqual(errno.EACCES, ctx.exception.errno)

    # -- Access check --

    def test_access_protected_raises_eacces(self) -> None:
        with self.assertRaises(FuseOSError) as ctx:
            self.ops.access("/secrets/token.txt", os.R_OK)
        self.assertEqual(errno.EACCES, ctx.exception.errno)

    # -- Allowed files: pass-through to original --

    def test_create_write_read_allowed_file(self) -> None:
        fh = self.ops.create("/src/new.py", 0o644)
        try:
            self.ops.write("/src/new.py", b"print('new')\n", 0, fh)
            self.ops.flush("/src/new.py", fh)
        finally:
            self.ops.release("/src/new.py", fh)

        self.assertEqual("print('new')\n", (self.root / "src" / "new.py").read_text(encoding="utf-8"))

    def test_rename_allowed_reflects_in_original(self) -> None:
        fh = self.ops.open("/src/main.py", os.O_RDWR)
        self.ops.release("/src/main.py", fh)

        self.ops.rename("/src/main.py", "/src/app.py")

        self.assertFalse((self.root / "src" / "main.py").exists())
        self.assertTrue((self.root / "src" / "app.py").exists())

    # -- Internal metadata: also protected --

    def test_open_internal_metadata_raises_eacces(self) -> None:
        with self.assertRaises(FuseOSError) as ctx:
            self.ops.open(f"/{INTERNAL_PROJECT_METADATA_DIR_NAME}/policy.json", os.O_RDONLY)
        self.assertEqual(errno.EACCES, ctx.exception.errno)


if __name__ == "__main__":
    unittest.main()
