from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.runner_environment_service import RunnerEnvironmentService


class RunnerEnvironmentServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = RunnerEnvironmentService()

    def test_build_strict_environment_scrubs_secret_and_unapproved_variables(self) -> None:
        with patch.dict(
            os.environ,
            {
                "PATH": "C:\\Windows\\System32",
                "OPENAI_API_KEY": "secret",
                "VISIBLE_FLAG": "1",
                "MY_PASSWORD_HINT": "nope",
            },
            clear=True,
        ):
            env = self.service.build_strict_environment()

        self.assertEqual("C:\\Windows\\System32", env["PATH"])
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("VISIBLE_FLAG", env)
        self.assertNotIn("MY_PASSWORD_HINT", env)
        self.assertEqual("1", env["AI_IDE_STRICT_PREVIEW"])

    def test_merge_environment_overrides_overlay_values(self) -> None:
        merged = self.service.merge_environment({"A": "1", "B": "2"}, {"B": "3", "C": "4"})

        self.assertEqual({"A": "1", "B": "3", "C": "4"}, merged)

    def test_argv_to_command_quotes_arguments_for_shell_round_trip(self) -> None:
        command = self.service.argv_to_command(["python", "-c", "print('hello world')"])

        self.assertIn("python", command)
        self.assertIn("hello world", command)

    def test_argv_to_command_uses_windows_cmdline_joining_on_windows(self) -> None:
        with patch("backend.runner_environment_service.os.name", "nt"):
            command = self.service.argv_to_command(["python", "-c", "print('hello world')"])

        self.assertIn("python", command)
        self.assertIn('"print(\'hello world\')"', command)
        self.assertNotIn("'print('", command)


if __name__ == "__main__":
    unittest.main()
