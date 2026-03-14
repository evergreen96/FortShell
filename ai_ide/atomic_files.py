from __future__ import annotations

import os
import time
from pathlib import Path


def atomic_replace(
    temp_path: Path,
    target_path: Path,
    *,
    retries: int = 5,
    retry_delay_seconds: float = 0.01,
) -> None:
    attempts = max(1, retries if os.name == "nt" else 1)
    last_error: PermissionError | None = None
    for attempt in range(attempts):
        try:
            temp_path.replace(target_path)
            return
        except PermissionError as exc:
            last_error = exc
            if os.name != "nt" or attempt == attempts - 1:
                raise
            time.sleep(retry_delay_seconds)
    if last_error is not None:
        raise last_error
