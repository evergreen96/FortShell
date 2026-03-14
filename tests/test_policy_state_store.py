from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.models import PolicyState
from core.policy_state_store import PolicyStateStore


class PolicyStateStoreTests(unittest.TestCase):
    def test_load_returns_default_state_when_file_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = PolicyStateStore(root)

            state = store.load()

            self.assertEqual([], state.deny_globs)
            self.assertEqual(1, state.version)

    def test_save_and_load_round_trip_policy_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = PolicyStateStore(root)
            store.save(PolicyState(deny_globs=["secrets/**", "env/"], version=4))

            state = store.load()

            self.assertEqual(["secrets/**", "env/"], state.deny_globs)
            self.assertEqual(4, state.version)

    def test_load_reuses_cached_state_when_file_is_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = PolicyStateStore(root)
            store.save(PolicyState(deny_globs=["secrets/**"], version=2))
            first_state = store.load()

            with patch.object(Path, "read_text", side_effect=AssertionError("should reuse cached state")):
                second_state = store.load()

        self.assertEqual(first_state.deny_globs, second_state.deny_globs)
        self.assertEqual(first_state.version, second_state.version)


if __name__ == "__main__":
    unittest.main()
