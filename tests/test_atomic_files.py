from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.atomic_files import atomic_replace


class AtomicFilesTests(unittest.TestCase):
    def test_atomic_replace_retries_permission_error_on_windows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / "state.json.tmp"
            target_path = Path(temp_dir) / "state.json"

            with patch("core.atomic_files.os.name", "nt"):
                with patch("core.atomic_files.time.sleep", return_value=None):
                    with patch.object(
                        Path,
                        "replace",
                        side_effect=[PermissionError("busy"), target_path],
                    ) as replace_mock:
                        atomic_replace(temp_path, target_path, retries=2)

            self.assertEqual(2, replace_mock.call_count)

    def test_atomic_replace_raises_after_retry_budget_is_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / "state.json.tmp"
            target_path = Path(temp_dir) / "state.json"

            with patch("core.atomic_files.os.name", "nt"):
                with patch("core.atomic_files.time.sleep", return_value=None):
                    with patch.object(Path, "replace", side_effect=PermissionError("busy")):
                        with self.assertRaises(PermissionError):
                            atomic_replace(temp_path, target_path, retries=2)


if __name__ == "__main__":
    unittest.main()
