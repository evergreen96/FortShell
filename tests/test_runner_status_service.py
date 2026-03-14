from __future__ import annotations

import json
import unittest

from ai_ide.platforms import PlatformAdapter, PlatformCapabilities, StrictSandboxProbe
from ai_ide.runner_status_service import RunnerStatusService
from ai_ide.strict_backend_health_service import StrictBackendHealth
from ai_ide.strict_backend_validation_cache import StrictBackendValidationSnapshot


class _FakePlatformAdapter(PlatformAdapter):
    name = "fake"

    def capabilities(self) -> PlatformCapabilities:
        return PlatformCapabilities(
            platform_name="fake",
            projection_supported=True,
            strict_sandbox_available=True,
            strict_sandbox_strategy="fake-sandbox",
        )

    def strict_probe(self) -> StrictSandboxProbe:
        return StrictSandboxProbe(
            platform_name="fake",
            ready=False,
            backend="wsl",
            status_code="access_denied",
            detail="wsl backend probe failed",
        )


class RunnerStatusServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = RunnerStatusService(
            _FakePlatformAdapter(),
            strict_boundary_scope_provider=lambda: "workspace-only",
        )

    def test_status_payload_reports_capabilities_and_probe(self) -> None:
        payload = self.service.status_payload("strict")

        self.assertEqual("strict", payload["mode"])
        self.assertEqual("fake", payload["platform"])
        self.assertTrue(payload["projection_supported"])
        self.assertEqual("fake-sandbox", payload["strict_strategy"])
        self.assertEqual("wsl", payload["strict_backend"])
        self.assertEqual("access_denied", payload["strict_backend_status"])
        self.assertEqual("wsl backend probe failed", payload["strict_backend_detail"])
        self.assertEqual("workspace-only", payload["strict_boundary_scope"])
        self.assertEqual("not_run", payload["strict_backend_validation_status"])
        self.assertTrue(payload["strict_preview_guarded"])

    def test_status_text_formats_machine_state_for_cli(self) -> None:
        text = self.service.status_text("projected")

        self.assertIn("mode=projected", text)
        self.assertIn("platform=fake", text)
        self.assertIn("strict_backend=wsl", text)
        self.assertIn("strict_backend_status=access_denied", text)
        self.assertIn("strict_boundary_scope=workspace-only", text)
        self.assertIn("strict_backend_validation_status=not_run", text)
        self.assertIn("strict_preview_guarded=true", text)

    def test_probe_text_formats_probe_for_cli(self) -> None:
        text = self.service.probe_text()

        self.assertEqual(
            "platform=fake backend=wsl ready=False status=access_denied detail=wsl backend probe failed",
            text,
        )

    def test_status_json_serializes_payload(self) -> None:
        payload = json.loads(self.service.status_json("projected"))

        self.assertEqual("projected", payload["mode"])
        self.assertEqual("access_denied", payload["strict_backend_status"])

    def test_status_payload_uses_contract_health_when_provider_is_available(self) -> None:
        service = RunnerStatusService(
            _FakePlatformAdapter(),
            strict_backend_health_provider=lambda: StrictBackendHealth(
                platform_name="fake",
                backend="wsl",
                probe_ready=True,
                probe_status="ready",
                probe_detail="probe ready",
                contract_valid=False,
                contract_status="invalid_contract",
                contract_detail="missing writable workspace bind",
                ready=False,
            ),
        )

        payload = service.status_payload("strict")

        self.assertFalse(payload["strict_backend_ready"])
        self.assertEqual("invalid_contract", payload["strict_backend_status"])
        self.assertEqual("missing writable workspace bind", payload["strict_backend_detail"])
        self.assertTrue(payload["strict_backend_probe_ready"])
        self.assertFalse(payload["strict_backend_contract_valid"])

    def test_status_payload_includes_current_validation_snapshot(self) -> None:
        service = RunnerStatusService(
            _FakePlatformAdapter(),
            strict_backend_health_provider=lambda: StrictBackendHealth(
                platform_name="fake",
                backend="wsl",
                probe_ready=True,
                probe_status="ready",
                probe_detail="probe ready",
                contract_valid=True,
                contract_status="valid",
                contract_detail="contract valid",
                ready=True,
            ),
            strict_backend_validation_provider=lambda: StrictBackendValidationSnapshot(
                status="passed",
                backend="wsl",
                ready=True,
                reason="ok",
                checked_at="2026-03-07T00:00:00Z",
                session_id="exec-1",
                restricted_token_status="enabled",
                write_boundary_status="enabled",
                read_boundary_status="enabled",
            ),
            execution_session_id_provider=lambda: "exec-1",
        )

        payload = service.status_payload("strict")

        self.assertEqual("passed", payload["strict_backend_validation_status"])
        self.assertEqual("ok", payload["strict_backend_validation_reason"])
        self.assertEqual("2026-03-07T00:00:00Z", payload["strict_backend_validation_checked_at"])
        self.assertEqual("enabled", payload["strict_backend_validation_restricted_token"])
        self.assertEqual("enabled", payload["strict_backend_validation_write_boundary"])
        self.assertEqual("enabled", payload["strict_backend_validation_read_boundary"])

    def test_status_payload_marks_validation_snapshot_stale_when_execution_session_changes(self) -> None:
        service = RunnerStatusService(
            _FakePlatformAdapter(),
            strict_backend_health_provider=lambda: StrictBackendHealth(
                platform_name="fake",
                backend="wsl",
                probe_ready=True,
                probe_status="ready",
                probe_detail="probe ready",
                contract_valid=True,
                contract_status="valid",
                contract_detail="contract valid",
                ready=True,
            ),
            strict_backend_validation_provider=lambda: StrictBackendValidationSnapshot(
                status="passed",
                backend="wsl",
                ready=True,
                reason="ok",
                checked_at="2026-03-07T00:00:00Z",
                session_id="exec-old",
            ),
            execution_session_id_provider=lambda: "exec-new",
        )

        payload = service.status_payload("strict")

        self.assertEqual("stale", payload["strict_backend_validation_status"])
        self.assertEqual(
            "execution session changed after last validation",
            payload["strict_backend_validation_reason"],
        )

    def test_status_payload_marks_validation_snapshot_stale_when_visible_workspace_changes(self) -> None:
        service = RunnerStatusService(
            _FakePlatformAdapter(),
            strict_backend_health_provider=lambda: StrictBackendHealth(
                platform_name="fake",
                backend="wsl",
                probe_ready=True,
                probe_status="ready",
                probe_detail="probe ready",
                contract_valid=True,
                contract_status="valid",
                contract_detail="contract valid",
                ready=True,
            ),
            strict_backend_validation_provider=lambda: StrictBackendValidationSnapshot(
                status="passed",
                backend="wsl",
                ready=True,
                reason="ok",
                checked_at="2026-03-07T00:00:00Z",
                session_id="exec-1",
                workspace_signature="sig-old",
            ),
            execution_session_id_provider=lambda: "exec-1",
            workspace_signature_provider=lambda: "sig-new",
        )

        payload = service.status_payload("strict")

        self.assertEqual("stale", payload["strict_backend_validation_status"])
        self.assertEqual(
            "visible workspace changed after last validation",
            payload["strict_backend_validation_reason"],
        )


if __name__ == "__main__":
    unittest.main()
