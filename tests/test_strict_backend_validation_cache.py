from __future__ import annotations

import unittest

from ai_ide.strict_backend_validation_cache import StrictBackendValidationCache


class StrictBackendValidationCacheTests(unittest.TestCase):
    def test_snapshot_defaults_to_not_run(self) -> None:
        cache = StrictBackendValidationCache()

        snapshot = cache.snapshot()

        self.assertEqual("not_run", snapshot.status)
        self.assertEqual("", snapshot.checked_at)

    def test_record_captures_validation_summary(self) -> None:
        cache = StrictBackendValidationCache()

        snapshot = cache.record(
            session_id="exec-1",
            backend="wsl",
            ready=False,
            status="skipped",
            reason="probe not ready",
            workspace_signature="sig-1",
        )

        self.assertEqual("skipped", snapshot.status)
        self.assertEqual("wsl", snapshot.backend)
        self.assertEqual("exec-1", snapshot.session_id)
        self.assertEqual("probe not ready", snapshot.reason)
        self.assertEqual("sig-1", snapshot.workspace_signature)
        self.assertTrue(snapshot.checked_at.endswith("Z"))


if __name__ == "__main__":
    unittest.main()
