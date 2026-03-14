from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_ide.workspace_visibility_models import VisibleWorkspaceState
from ai_ide.workspace_visibility_state_store import WorkspaceVisibilityStateStore


class WorkspaceVisibilityStateStoreTests(unittest.TestCase):
    def test_load_returns_none_when_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorkspaceVisibilityStateStore(Path(temp_dir) / "workspace" / "state.json")

            state = store.load()

        self.assertIsNone(state)

    def test_save_and_load_round_trip_visible_workspace_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorkspaceVisibilityStateStore(Path(temp_dir) / "workspace" / "state.json")
            original = VisibleWorkspaceState(signature="sig-123", entry_count=42, policy_version=3)

            store.save(original)
            loaded = store.load()

        self.assertEqual(original, loaded)

    def test_load_reuses_cached_state_when_file_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = WorkspaceVisibilityStateStore(Path(temp_dir) / "workspace" / "state.json")
            original = VisibleWorkspaceState(signature="sig-123", entry_count=42, policy_version=3)

            store.save(original)
            first_loaded = store.load()

            with patch.object(Path, "read_text", side_effect=AssertionError("should reuse cached state")):
                second_loaded = store.load()

        self.assertEqual(first_loaded, second_loaded)


if __name__ == "__main__":
    unittest.main()
