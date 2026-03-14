from __future__ import annotations

from collections.abc import Callable
from threading import Event, Lock, Thread


class QueueSignalWorkspaceVisibilityWatcher:
    def __init__(self) -> None:
        self._lock = Lock()
        self._set_thread(None)
        self._set_stop_event(None)
        self._set_notify_event(None)
        self._set_on_event(None)

    def start(self, on_event: Callable[[], None]) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._set_on_event(on_event)
                return
            self._set_on_event(on_event)
            stop_event = Event()
            notify_event = Event()
            thread = Thread(
                target=self._run_loop,
                args=(stop_event, notify_event),
                daemon=True,
                name="workspace-visibility-watcher",
            )
            self._set_stop_event(stop_event)
            self._set_notify_event(notify_event)
            self._set_thread(thread)
            thread.start()

    def close(self) -> None:
        with self._lock:
            thread = self._thread
            stop_event = self._stop_event
            notify_event = self._notify_event
            self._set_thread(None)
            self._set_stop_event(None)
            self._set_notify_event(None)
            self._set_on_event(None)
        if thread is None or stop_event is None or notify_event is None:
            return
        stop_event.set()
        notify_event.set()
        thread.join(timeout=1)

    def notify_change(self) -> None:
        with self._lock:
            notify_event = self._notify_event
        if notify_event is not None:
            notify_event.set()

    def _run_loop(self, stop_event: Event, notify_event: Event) -> None:
        while True:
            notify_event.wait()
            notify_event.clear()
            if stop_event.is_set():
                return
            callback = self._on_event
            if callback is not None:
                callback()

    def _set_thread(self, thread: Thread | None) -> None:
        self._thread = thread

    def _set_stop_event(self, stop_event: Event | None) -> None:
        self._stop_event = stop_event

    def _set_notify_event(self, notify_event: Event | None) -> None:
        self._notify_event = notify_event

    def _set_on_event(self, on_event: Callable[[], None] | None) -> None:
        self._on_event = on_event
