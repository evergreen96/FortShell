from __future__ import annotations

import threading
import unittest

from backend.workspace_visibility_watcher import QueueSignalWorkspaceVisibilityWatcher


class WorkspaceVisibilityWatcherTests(unittest.TestCase):
    def test_queue_signal_watcher_emits_callback_when_notified(self) -> None:
        watcher = QueueSignalWorkspaceVisibilityWatcher()
        signaled = threading.Event()
        calls: list[str] = []

        def on_event() -> None:
            calls.append("event")
            signaled.set()

        watcher.start(on_event)
        try:
            watcher.notify_change()
            self.assertTrue(signaled.wait(timeout=1))
            self.assertEqual(["event"], calls)
        finally:
            watcher.close()

    def test_queue_signal_watcher_close_stops_future_notifications(self) -> None:
        watcher = QueueSignalWorkspaceVisibilityWatcher()
        signaled = threading.Event()
        calls: list[str] = []

        def on_event() -> None:
            calls.append("event")
            signaled.set()

        watcher.start(on_event)
        watcher.close()
        watcher.notify_change()

        self.assertFalse(signaled.wait(timeout=0.2))
        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
