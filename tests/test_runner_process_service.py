from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from backend.runner_process_service import RunnerProcessService


class RunnerProcessServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.service = RunnerProcessService()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_run_subprocess_sets_runner_mode_and_reports_working_directory(self) -> None:
        result = self.service.run_subprocess(
            [sys.executable, "-c", "import os; print(os.environ['AI_IDE_RUNNER_MODE'])"],
            self.root,
            mode="projected",
            backend="projected",
            shell=False,
        )

        self.assertEqual(0, result.returncode)
        self.assertEqual("projected", result.mode)
        self.assertIn("projected", result.stdout)
        self.assertEqual(str(self.root), result.working_directory)

    def test_start_subprocess_creates_log_artifacts_under_requested_root(self) -> None:
        artifact_root = self.root / "artifacts"
        handle = self.service.start_subprocess(
            [
                sys.executable,
                "-c",
                "import sys; print('hello'); print('warn', file=sys.stderr)",
            ],
            self.root,
            mode="strict",
            backend="strict-preview",
            shell=False,
            artifact_root=artifact_root,
        )
        handle.process.wait(timeout=5)
        if handle.stdin_file is not None:
            handle.stdin_file.close()
        handle.stdout_file.close()
        handle.stderr_file.close()

        self.assertTrue(handle.stdout_path.exists())
        self.assertTrue(handle.stderr_path.exists())
        self.assertTrue(str(handle.stdout_path).startswith(str(artifact_root)))
        self.assertIn("hello", handle.stdout_path.read_text(encoding="utf-8"))
        self.assertIn("warn", handle.stderr_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
