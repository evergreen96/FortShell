"""Tests for Dokan driver check."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.windows.dokan_driver_check import check_dokan_driver


@unittest.skipUnless(os.name == "nt", "Windows only")
class DokanDriverCheckTests(unittest.TestCase):
    def test_check_returns_status_object(self) -> None:
        status = check_dokan_driver()

        self.assertIsInstance(status.installed, bool)
        self.assertIsInstance(status.fusepy_available, bool)
        if status.installed:
            self.assertIsNotNone(status.dll_path)
            self.assertIsNone(status.install_hint)
        else:
            self.assertIsNotNone(status.install_hint)
            self.assertIn("Dokan", status.install_hint)

    def test_missing_driver_gives_install_hint(self) -> None:
        with patch("backend.windows.dokan_driver_check._find_dokan_dll", return_value=None):
            status = check_dokan_driver()

        self.assertFalse(status.installed)
        self.assertIn("winget", status.install_hint)
        self.assertIn("dokan-dev.github.io", status.install_hint)

    def test_driver_present_but_no_fusepy(self) -> None:
        with patch("backend.windows.dokan_driver_check._find_dokan_dll", return_value="dokan2.dll"):
            with patch("backend.windows.dokan_driver_check._check_fusepy", return_value=False):
                status = check_dokan_driver()

        self.assertTrue(status.installed)
        self.assertIn("fusepy", status.install_hint)

    def test_everything_present_no_hint(self) -> None:
        with patch("backend.windows.dokan_driver_check._find_dokan_dll", return_value="dokan2.dll"):
            with patch("backend.windows.dokan_driver_check._check_fusepy", return_value=True):
                status = check_dokan_driver()

        self.assertTrue(status.installed)
        self.assertIsNone(status.install_hint)


if __name__ == "__main__":
    unittest.main()
