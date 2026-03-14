from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ide.workspace_index_state_store import WorkspaceIndexStateStore
from ai_ide.workspace_models import WorkspaceIndexEntry, WorkspaceIndexSnapshot


class WorkspaceIndexStateStoreTests(unittest.TestCase):
    def test_load_returns_default_snapshot_when_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorkspaceIndexStateStore(Path(temp_dir) / "workspace" / "index.json")

            snapshot = store.load()

            self.assertEqual(0, snapshot.policy_version)
            self.assertEqual("", snapshot.signature)
            self.assertEqual([], snapshot.entries)

    def test_save_and_load_round_trip_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorkspaceIndexStateStore(Path(temp_dir) / "workspace" / "index.json")
            saved = WorkspaceIndexSnapshot(
                policy_version=3,
                signature="sig-123",
                entries=[
                    WorkspaceIndexEntry(
                        path="notes/todo.txt",
                        is_dir=False,
                        size=12,
                        modified_ns=42,
                    )
                ],
            )

            store.save(saved)
            restored = store.load()

            self.assertEqual(saved, restored)

    def test_load_reuses_cached_snapshot_when_file_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorkspaceIndexStateStore(Path(temp_dir) / "workspace" / "index.json")
            saved = WorkspaceIndexSnapshot(
                policy_version=3,
                signature="sig-123",
                entries=[
                    WorkspaceIndexEntry(
                        path="notes/todo.txt",
                        is_dir=False,
                        size=12,
                        modified_ns=42,
                    )
                ],
            )

            store.save(saved)
            first_snapshot = store.load()

            with patch.object(Path, "read_text", side_effect=AssertionError("should reuse cached snapshot")):
                second_snapshot = store.load()

        self.assertEqual(first_snapshot, second_snapshot)


if __name__ == "__main__":
    unittest.main()
