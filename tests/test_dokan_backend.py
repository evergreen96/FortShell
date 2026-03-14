from __future__ import annotations

import errno
import os
import tempfile
import unittest
from pathlib import Path

from fuse import FuseOSError

from ai_ide.dokan_backend import DokanFilteredOperations
from ai_ide.internal import INTERNAL_PROJECT_METADATA_DIR_NAME, INTERNAL_RUNTIME_DIR_NAME
from ai_ide.policy import PolicyEngine
from ai_ide.workspace_access_service import WorkspaceAccessService


class DokanFilteredOperationsTests(unittest.TestCase):
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

    def test_readdir_hides_denied_and_internal_entries(self) -> None:
        entries = self.ops.readdir("/", None)

        self.assertIn("src", entries)
        self.assertNotIn("secrets", entries)
        self.assertNotIn(INTERNAL_PROJECT_METADATA_DIR_NAME, entries)
        self.assertNotIn(INTERNAL_RUNTIME_DIR_NAME, entries)

    def test_open_hidden_path_raises_enoent(self) -> None:
        with self.assertRaises(FuseOSError) as ctx:
            self.ops.open("/secrets/token.txt", os.O_RDONLY)

        self.assertEqual(errno.ENOENT, ctx.exception.errno)

    def test_create_write_and_read_through_to_original_file(self) -> None:
        fh = self.ops.create("/src/new.py", 0o644)
        try:
            written = self.ops.write("/src/new.py", b"print('new')\n", 0, fh)
            self.assertEqual(len(b"print('new')\n"), written)
            self.ops.flush("/src/new.py", fh)
        finally:
            self.ops.release("/src/new.py", fh)

        self.assertEqual("print('new')\n", (self.root / "src" / "new.py").read_text(encoding="utf-8"))

    def test_rename_reflects_in_original_workspace(self) -> None:
        fh = self.ops.open("/src/main.py", os.O_RDWR)
        self.ops.release("/src/main.py", fh)

        self.ops.rename("/src/main.py", "/src/app.py")

        self.assertFalse((self.root / "src" / "main.py").exists())
        self.assertTrue((self.root / "src" / "app.py").exists())


if __name__ == "__main__":
    unittest.main()
