from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_ide.runner_host_service import RunnerHostService
from ai_ide.runner_models import RunnerLaunchResult, RunnerProcessHandle, RunnerResult


class RunnerHostServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_root = Path(self.temp_dir.name)
        self.calls: list[tuple[str, tuple, dict]] = []
        self.service = RunnerHostService(
            self.project_root,
            run_subprocess=self._record_run,
            start_subprocess=self._record_start,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_run_uses_host_mode_and_backend(self) -> None:
        result = self.service.run("dir")

        self.assertEqual("host", result.mode)
        self.assertEqual("host", result.backend)
        name, args, kwargs = self.calls[-1]
        self.assertEqual("run", name)
        self.assertEqual(self.project_root.resolve(), args[1])
        self.assertEqual("host", kwargs["mode"])
        self.assertEqual("host", kwargs["backend"])

    def test_run_process_passes_env_overlay_without_shell(self) -> None:
        result = self.service.run_process(["python", "-V"], env={"X": "1"})

        self.assertEqual("host", result.mode)
        name, args, kwargs = self.calls[-1]
        self.assertEqual("run", name)
        self.assertEqual(["python", "-V"], args[0])
        self.assertEqual({"X": "1"}, kwargs["env"])
        self.assertFalse(kwargs["shell"])

    def test_start_process_uses_host_artifact_root(self) -> None:
        result = self.service.start_process(["python", "-V"], env={"X": "1"})

        self.assertTrue(result.started)
        self.assertIsInstance(result, RunnerLaunchResult)
        name, args, kwargs = self.calls[-1]
        self.assertEqual("start", name)
        self.assertEqual(self.project_root.resolve() / ".ai_ide_processes", kwargs["artifact_root"])
        self.assertFalse(kwargs["shell"])

    def _record_run(self, *args, **kwargs):
        self.calls.append(("run", args, kwargs))
        return RunnerResult(
            mode=kwargs["mode"],
            backend=kwargs["backend"],
            returncode=0,
            stdout="ok",
            stderr="",
            working_directory=str(args[1]),
        )

    def _record_start(self, *args, **kwargs):
        self.calls.append(("start", args, kwargs))
        return RunnerProcessHandle(
            run_id="proc-1",
            mode=kwargs["mode"],
            backend=kwargs["backend"],
            working_directory=str(args[1]),
            process=None,  # type: ignore[arg-type]
            stdin_file=None,
            stdout_path=Path("stdout.log"),
            stderr_path=Path("stderr.log"),
            stdout_file=None,  # type: ignore[arg-type]
            stderr_file=None,  # type: ignore[arg-type]
        )


if __name__ == "__main__":
    unittest.main()
