from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from pathlib import Path

from ai_ide.internal import INTERNAL_PROJECT_METADATA_DIR_NAME
from ai_ide.platforms import StrictBackendFixtureExpectations
from ai_ide.runner_models import RunnerResult
from ai_ide.strict_backend_health_service import StrictBackendHealth


@dataclass(frozen=True)
class StrictBackendFixtureCheck:
    name: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class StrictBackendFixtureReport:
    status: str
    backend: str
    ready: bool
    checks: list[StrictBackendFixtureCheck]
    reason: str
    working_directory: str
    stdout: str
    stderr: str
    restricted_token_status: str = ""
    write_boundary_status: str = ""
    read_boundary_status: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "backend": self.backend,
            "ready": self.ready,
            "reason": self.reason,
            "working_directory": self.working_directory,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "restricted_token_status": self.restricted_token_status,
            "write_boundary_status": self.write_boundary_status,
            "read_boundary_status": self.read_boundary_status,
            "checks": [check.to_dict() for check in self.checks],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True)


@dataclass(frozen=True)
class StrictBackendFixturePaths:
    metadata_dir: Path
    metadata_dir_created: bool
    denied_relative_path: str
    denied_host_path: Path
    denied_backend_path: str
    write_target_host_path: Path
    write_target_backend_path: str


class StrictBackendFixtureService:
    _FIXTURE_FILENAME = ".ai_ide_strict_fixture.txt"
    _MARKER_PREFIX = "__AI_IDE_FIXTURE__"

    def __init__(
        self,
        *,
        project_root: Path,
        projection_manager,
        session_manager,
        strict_backend_health_provider,
        strict_runner_run,
        strict_backend_visible_path,
        strict_backend_fixture_expectations=lambda backend: StrictBackendFixtureExpectations(
            sandbox_root="/workspace",
            home_prefix="/tmp/ai-ide-home",
            cache_prefix="/tmp/ai-ide-cache",
        ),
    ) -> None:
        self.project_root = project_root.resolve()
        self.projection_manager = projection_manager
        self.session_manager = session_manager
        self.strict_backend_health_provider = strict_backend_health_provider
        self.strict_runner_run = strict_runner_run
        self.strict_backend_visible_path = strict_backend_visible_path
        self.strict_backend_fixture_expectations = strict_backend_fixture_expectations

    def run(self) -> StrictBackendFixtureReport:
        health: StrictBackendHealth = self.strict_backend_health_provider()
        if not health.ready:
            return StrictBackendFixtureReport(
                status="skipped",
                backend=health.backend,
                ready=False,
                checks=[],
                reason=health.contract_detail if health.probe_ready else health.probe_detail,
                working_directory="",
                stdout="",
                stderr="",
            )

        projection_root = self.projection_manager.projection_root(self.session_manager.current_session_id)
        fixture_path = projection_root / self._FIXTURE_FILENAME
        fixture_paths = self._prepare_fixture_paths(health.backend)
        if fixture_path.exists():
            fixture_path.unlink()

        try:
            result = self.strict_runner_run(
                self._fixture_command(
                    denied_relative_path=fixture_paths.denied_relative_path,
                    denied_backend_path=fixture_paths.denied_backend_path,
                    write_target_backend_path=fixture_paths.write_target_backend_path,
                )
            )
            markers = self._parse_markers(result.stdout)
            expectations = self.strict_backend_fixture_expectations(health.backend)

            checks = [
                StrictBackendFixtureCheck(
                    "backend_used",
                    result.backend == health.backend,
                    f"backend={result.backend} expected={health.backend}",
                ),
                StrictBackendFixtureCheck(
                    "sandbox_root",
                    markers.get("sandbox") == expectations.sandbox_root,
                    f"sandbox={markers.get('sandbox', '(missing)')}",
                ),
                StrictBackendFixtureCheck(
                    "isolated_home",
                    markers.get("home", "").startswith(expectations.home_prefix),
                    f"home={markers.get('home', '(missing)')}",
                ),
                StrictBackendFixtureCheck(
                    "isolated_cache",
                    markers.get("cache", "").startswith(expectations.cache_prefix),
                    f"cache={markers.get('cache', '(missing)')}",
                ),
                StrictBackendFixtureCheck(
                    "denied_relative_path_hidden",
                    markers.get("denied_relative") == "hidden",
                    f"denied_relative={markers.get('denied_relative', '(missing)')}",
                ),
                StrictBackendFixtureCheck(
                    "denied_direct_path_hidden",
                    markers.get("denied_direct") == "hidden",
                    f"denied_direct={markers.get('denied_direct', '(missing)')}",
                ),
                StrictBackendFixtureCheck(
                    "direct_write_blocked",
                    markers.get("direct_write") == "blocked",
                    f"direct_write={markers.get('direct_write', '(missing)')}",
                ),
                StrictBackendFixtureCheck(
                    "direct_write_target_untouched",
                    fixture_paths.write_target_host_path.read_text(encoding="utf-8") == "original",
                    f"write_target={fixture_paths.write_target_host_path}",
                ),
                StrictBackendFixtureCheck(
                    "projection_write",
                    fixture_path.exists(),
                    f"projection_file={fixture_path}",
                ),
                StrictBackendFixtureCheck(
                    "host_project_untouched",
                    not (self.project_root / self._FIXTURE_FILENAME).exists(),
                    f"host_file={(self.project_root / self._FIXTURE_FILENAME)}",
                ),
            ]

            reason = "ok"
            status = "passed"
            if result.returncode != 0:
                status = "failed"
                reason = f"backend command failed with exit code {result.returncode}"
            elif any(not check.passed for check in checks):
                status = "failed"
                failed = next(check for check in checks if not check.passed)
                reason = f"{failed.name} failed"

            return StrictBackendFixtureReport(
                status=status,
                backend=result.backend,
                ready=health.ready,
                checks=checks,
                reason=reason,
                working_directory=result.working_directory,
                stdout=result.stdout,
                stderr=result.stderr,
                restricted_token_status=markers.get("restricted_token", ""),
                write_boundary_status=markers.get("write_boundary", ""),
                read_boundary_status=markers.get("read_boundary", ""),
            )
        finally:
            try:
                if fixture_path.exists():
                    fixture_path.unlink()
            except OSError:
                pass
            self._cleanup_fixture_paths(fixture_paths)

    def _fixture_command(
        self,
        *,
        denied_relative_path: str,
        denied_backend_path: str,
        write_target_backend_path: str,
    ) -> str:
        quoted_direct_path = shlex.quote(denied_backend_path)
        quoted_write_target = shlex.quote(write_target_backend_path)
        return (
            f"printf '{self._MARKER_PREFIX} sandbox=%s\\n' \"$AI_IDE_SANDBOX_ROOT\"; "
            f"printf '{self._MARKER_PREFIX} home=%s\\n' \"$HOME\"; "
            f"printf '{self._MARKER_PREFIX} cache=%s\\n' \"$XDG_CACHE_HOME\"; "
            f"if [ -e {shlex.quote(denied_relative_path)} ]; "
            f"then printf '{self._MARKER_PREFIX} denied_relative=visible\\n'; "
            f"else printf '{self._MARKER_PREFIX} denied_relative=hidden\\n'; fi; "
            f"if [ -e {quoted_direct_path} ]; then printf '{self._MARKER_PREFIX} denied_direct=visible\\n'; "
            f"else printf '{self._MARKER_PREFIX} denied_direct=hidden\\n'; fi; "
            f"if printf sandbox-write > {quoted_write_target} 2>/dev/null; "
            f"then printf '{self._MARKER_PREFIX} direct_write=allowed\\n'; "
            f"else printf '{self._MARKER_PREFIX} direct_write=blocked\\n'; fi; "
            f"printf fixture > {self._FIXTURE_FILENAME}"
        )

    def _parse_markers(self, stdout: str) -> dict[str, str]:
        markers: dict[str, str] = {}
        for line in stdout.splitlines():
            if not line.startswith(f"{self._MARKER_PREFIX} "):
                continue
            payload = line[len(self._MARKER_PREFIX) + 1 :]
            if "=" not in payload:
                continue
            name, value = payload.split("=", 1)
            markers[name] = value
        return markers

    def _prepare_fixture_paths(self, backend: str) -> StrictBackendFixturePaths:
        metadata_dir = self.project_root / INTERNAL_PROJECT_METADATA_DIR_NAME
        metadata_dir_created = not metadata_dir.exists()
        metadata_dir.mkdir(parents=True, exist_ok=True)

        denied_relative = Path(INTERNAL_PROJECT_METADATA_DIR_NAME) / "strict-backend-denied.txt"
        denied_host_path = self.project_root / denied_relative
        denied_host_path.write_text("hidden", encoding="utf-8")

        write_target_host_path = metadata_dir / "strict-backend-direct-write.txt"
        write_target_host_path.write_text("original", encoding="utf-8")

        return StrictBackendFixturePaths(
            metadata_dir=metadata_dir,
            metadata_dir_created=metadata_dir_created,
            denied_relative_path=denied_relative.as_posix(),
            denied_host_path=denied_host_path,
            denied_backend_path=self.strict_backend_visible_path(denied_host_path, backend),
            write_target_host_path=write_target_host_path,
            write_target_backend_path=self.strict_backend_visible_path(write_target_host_path, backend),
        )

    def _cleanup_fixture_paths(self, fixture_paths: StrictBackendFixturePaths) -> None:
        for path in [fixture_paths.denied_host_path, fixture_paths.write_target_host_path]:
            try:
                if path.exists():
                    path.unlink()
            except OSError:
                pass
        if fixture_paths.metadata_dir_created:
            try:
                fixture_paths.metadata_dir.rmdir()
            except OSError:
                pass
