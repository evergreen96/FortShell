from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.windows.platforms import WINDOWS_STRICT_HELPER_ENV, StrictSandboxProbe, get_platform_adapter
from backend.windows.windows_strict_helper_resolution import WINDOWS_STRICT_HELPER_RUST_DEV
from backend.windows.windows_strict_helper_protocol import STDIO_PROXY_FLAG


class PlatformAdapterTests(unittest.TestCase):
    def test_windows_adapter_capabilities(self) -> None:
        capabilities = get_platform_adapter("Windows").capabilities()
        self.assertEqual("windows", capabilities.platform_name)
        self.assertTrue(capabilities.projection_supported)
        self.assertTrue(capabilities.strict_sandbox_available)

    def test_linux_adapter_capabilities(self) -> None:
        capabilities = get_platform_adapter("Linux").capabilities()
        self.assertEqual("linux", capabilities.platform_name)
        self.assertIn("landlock", capabilities.strict_sandbox_strategy)

    def test_macos_adapter_capabilities(self) -> None:
        capabilities = get_platform_adapter("Darwin").capabilities()
        self.assertEqual("macos", capabilities.platform_name)
        self.assertIn("vm", capabilities.strict_sandbox_strategy)

    def test_unknown_platform_falls_back_to_generic(self) -> None:
        capabilities = get_platform_adapter("Plan9").capabilities()
        self.assertEqual("generic", capabilities.platform_name)
        self.assertFalse(capabilities.strict_sandbox_available)

    def test_platform_default_workspace_visibility_watcher_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir) / "project"
            runtime_root = Path(temp_dir) / "runtime"
            project_root.mkdir()
            runtime_root.mkdir()

            self.assertIsNone(get_platform_adapter("Plan9").workspace_visibility_watcher(project_root, runtime_root))
            self.assertIsNone(get_platform_adapter("Windows").workspace_visibility_watcher(project_root, runtime_root))
            self.assertIsNone(get_platform_adapter("Linux").workspace_visibility_watcher(project_root, runtime_root))
            self.assertIsNone(get_platform_adapter("Darwin").workspace_visibility_watcher(project_root, runtime_root))

    def test_windows_strict_probe_reports_wsl_when_available(self) -> None:
        adapter = get_platform_adapter("Windows")
        completed = subprocess.CompletedProcess(
            args=["wsl.exe", "--status"],
            returncode=0,
            stdout="ok",
            stderr="",
        )
        with patch("shutil.which", side_effect=lambda name: "C:/Windows/System32/wsl.exe" if name in {"wsl", "wsl.exe"} else None):
            with patch("subprocess.run", return_value=completed):
                probe = adapter.strict_probe()

        self.assertTrue(probe.ready)
        self.assertEqual("wsl", probe.backend)
        self.assertEqual("ready", probe.status_code)

    def test_windows_strict_probe_prefers_native_helper_when_configured(self) -> None:
        adapter = get_platform_adapter("Windows")

        with patch.dict("os.environ", {WINDOWS_STRICT_HELPER_ENV: "C:/tools/ai-ide-restricted-host-helper.exe"}):
            with patch("subprocess.run") as subprocess_run:
                probe = adapter.strict_probe()

        self.assertTrue(probe.ready)
        self.assertEqual("restricted-host-helper", probe.backend)
        self.assertEqual("ready", probe.status_code)
        subprocess_run.assert_not_called()

    def test_windows_strict_probe_reports_not_ready_when_access_denied(self) -> None:
        adapter = get_platform_adapter("Windows")
        completed = subprocess.CompletedProcess(
            args=["wsl.exe", "--status"],
            returncode=1,
            stdout="",
            stderr="E_ACCESSDENIED",
        )
        with patch("shutil.which", side_effect=lambda name: "C:/Windows/System32/wsl.exe" if name in {"wsl", "wsl.exe"} else None):
            with patch("subprocess.run", return_value=completed):
                probe = adapter.strict_probe()

        self.assertFalse(probe.ready)
        self.assertEqual("wsl", probe.backend)
        self.assertEqual("access_denied", probe.status_code)
        self.assertIn("E_ACCESSDENIED", probe.detail)

    def test_linux_strict_probe_reports_no_backend_when_missing(self) -> None:
        adapter = get_platform_adapter("Linux")
        with patch("shutil.which", return_value=None):
            probe = adapter.strict_probe()

        self.assertFalse(probe.ready)
        self.assertEqual("none", probe.backend)
        self.assertEqual("not_found", probe.status_code)

    def test_linux_strict_probe_reports_bwrap_when_available(self) -> None:
        adapter = get_platform_adapter("Linux")

        with patch("shutil.which", side_effect=lambda name: "/usr/bin/bwrap" if name == "bwrap" else None):
            probe = adapter.strict_probe()

        self.assertTrue(probe.ready)
        self.assertEqual("bwrap", probe.backend)
        self.assertEqual("ready", probe.status_code)
        self.assertIn("strict backend execution", probe.detail)

    def test_linux_strict_probe_reports_unshare_as_detected_but_not_ready(self) -> None:
        adapter = get_platform_adapter("Linux")

        with patch("shutil.which", side_effect=lambda name: "/usr/bin/unshare" if name == "unshare" else None):
            probe = adapter.strict_probe()

        self.assertFalse(probe.ready)
        self.assertEqual("unshare", probe.backend)
        self.assertEqual("not_wired", probe.status_code)
        self.assertIn("wired", probe.detail)

    def test_windows_strict_backend_invocation_uses_wsl_workspace_path(self) -> None:
        adapter = get_platform_adapter("Windows")
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir) / "projection"
            projected_root.mkdir()

            with patch.object(
                adapter,
                "strict_probe",
                return_value=StrictSandboxProbe("windows", True, "wsl", "ready", "ready"),
            ):
                with patch(
                    "shutil.which",
                    side_effect=lambda name: "C:/Windows/System32/wsl.exe" if name in {"wsl", "wsl.exe"} else None,
                ):
                    invocation = adapter.strict_backend_invocation("printf backend-ok", projected_root)

        self.assertIsNotNone(invocation)
        assert invocation is not None
        self.assertEqual("wsl", invocation.backend)
        self.assertEqual(projected_root, invocation.host_working_directory)
        self.assertTrue(invocation.working_directory.startswith("/mnt/"))
        self.assertIn("mkdir -p /tmp/ai-ide-home /tmp/ai-ide-cache", invocation.command[-1])
        self.assertIn("AI_IDE_STRICT_BACKEND=wsl", invocation.command[-1])
        self.assertIn("AI_IDE_SANDBOX_ROOT=/workspace", invocation.command[-1])
        self.assertIn("HOME=/tmp/ai-ide-home", invocation.command[-1])
        self.assertIn("XDG_CACHE_HOME=/tmp/ai-ide-cache", invocation.command[-1])
        self.assertIn(invocation.working_directory, invocation.command[-1])

    def test_windows_strict_backend_invocation_passes_env_overlay(self) -> None:
        adapter = get_platform_adapter("Windows")
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir) / "projection"
            projected_root.mkdir()

            with patch.object(
                adapter,
                "strict_probe",
                return_value=StrictSandboxProbe("windows", True, "wsl", "ready", "ready"),
            ):
                with patch(
                    "shutil.which",
                    side_effect=lambda name: "C:/Windows/System32/wsl.exe" if name in {"wsl", "wsl.exe"} else None,
                ):
                    invocation = adapter.strict_backend_invocation(
                        "printf backend-ok",
                        projected_root,
                        env={"AI_IDE_AGENT_SESSION_ID": "agent-1234"},
                    )

        self.assertIsNotNone(invocation)
        assert invocation is not None
        self.assertIn("AI_IDE_AGENT_SESSION_ID=agent-1234", invocation.command[-1])

    def test_windows_wsl_invocation_ignores_reserved_env_overrides(self) -> None:
        adapter = get_platform_adapter("Windows")
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir) / "projection"
            projected_root.mkdir()

            with patch.object(
                adapter,
                "strict_probe",
                return_value=StrictSandboxProbe("windows", True, "wsl", "ready", "ready"),
            ):
                with patch(
                    "shutil.which",
                    side_effect=lambda name: "C:/Windows/System32/wsl.exe" if name in {"wsl", "wsl.exe"} else None,
                ):
                    invocation = adapter.strict_backend_invocation(
                        "printf backend-ok",
                        projected_root,
                        env={
                            "AI_IDE_AGENT_SESSION_ID": "agent-1234",
                            "AI_IDE_STRICT_BACKEND": "evil",
                            "PATH": "C:/Users/Public",
                            "HOME": "C:/Users/Public",
                            "NoDefaultCurrentDirectoryInExePath": "0",
                        },
                    )

        assert invocation is not None
        self.assertIn("AI_IDE_AGENT_SESSION_ID=agent-1234", invocation.command[-1])
        self.assertIn("AI_IDE_STRICT_BACKEND=wsl", invocation.command[-1])
        self.assertIn("PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", invocation.command[-1])
        self.assertIn("HOME=/tmp/ai-ide-home", invocation.command[-1])
        self.assertNotIn("AI_IDE_STRICT_BACKEND=evil", invocation.command[-1])
        self.assertNotIn("PATH=C:/Users/Public", invocation.command[-1])
        self.assertNotIn("HOME=C:/Users/Public", invocation.command[-1])
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath=0", invocation.command[-1])

    def test_windows_strict_backend_visible_path_maps_to_wsl_mount(self) -> None:
        adapter = get_platform_adapter("Windows")
        with tempfile.TemporaryDirectory() as temp_dir:
            host_path = Path(temp_dir) / "project" / ".ai-ide" / "strict-backend-denied.txt"
            host_path.parent.mkdir(parents=True)
            host_path.write_text("hidden", encoding="utf-8")

            visible_path = adapter.strict_backend_visible_path(host_path, "wsl")

        self.assertTrue(visible_path.startswith("/mnt/"))
        self.assertIn("/.ai-ide/strict-backend-denied.txt", visible_path)

    def test_windows_helper_visible_path_is_opaque_and_not_host_literal(self) -> None:
        adapter = get_platform_adapter("Windows")
        with tempfile.TemporaryDirectory() as temp_dir:
            host_path = Path(temp_dir) / "project" / ".ai-ide" / "strict-backend-denied.txt"
            host_path.parent.mkdir(parents=True)
            host_path.write_text("hidden", encoding="utf-8")

            visible_path = adapter.strict_backend_visible_path(host_path, "restricted-host-helper")

        self.assertTrue(visible_path.startswith("aiide-helper://host-path/"))
        self.assertNotIn(str(host_path.resolve()).replace("\\", "/").lower(), visible_path.lower())

    def test_windows_strict_backend_invocation_uses_native_helper_when_configured(self) -> None:
        adapter = get_platform_adapter("Windows")
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir) / "projection"
            projected_root.mkdir()

            with patch.object(
                adapter,
                "strict_probe",
                return_value=StrictSandboxProbe("windows", True, "restricted-host-helper", "ready", "ready"),
            ):
                with patch.dict(
                    "os.environ",
                    {WINDOWS_STRICT_HELPER_ENV: "C:/tools/ai-ide-restricted-host-helper.exe"},
                ):
                    invocation = adapter.strict_backend_invocation(
                        "printf backend-ok",
                        projected_root,
                        env={"AI_IDE_AGENT_SESSION_ID": "agent-1234"},
                    )

        self.assertIsNotNone(invocation)
        assert invocation is not None
        self.assertEqual("restricted-host-helper", invocation.backend)
        self.assertEqual(projected_root, invocation.host_working_directory)
        self.assertEqual("/workspace", invocation.working_directory)
        self.assertEqual("C:/tools/ai-ide-restricted-host-helper.exe", invocation.command[0])
        self.assertIn("--workspace", invocation.command)
        self.assertIn(str(projected_root), invocation.command)
        self.assertIn("AI_IDE_STRICT_BACKEND", invocation.command)
        self.assertIn("restricted-host-helper", invocation.command)
        self.assertIn("AI_IDE_BOUNDARY_SCOPE", invocation.command)
        self.assertIn("workspace-only", invocation.command)
        self.assertIn("AI_IDE_AGENT_SESSION_ID", invocation.command)
        self.assertIn("agent-1234", invocation.command)
        self.assertEqual("printf backend-ok", invocation.command[-1])

    def test_windows_helper_invocation_ignores_reserved_env_overrides(self) -> None:
        adapter = get_platform_adapter("Windows")
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir) / "projection"
            projected_root.mkdir()

            with patch.object(
                adapter,
                "strict_probe",
                return_value=StrictSandboxProbe("windows", True, "restricted-host-helper", "ready", "ready"),
            ):
                with patch.dict(
                    "os.environ",
                    {WINDOWS_STRICT_HELPER_ENV: "C:/tools/ai-ide-restricted-host-helper.exe"},
                ):
                    invocation = adapter.strict_backend_invocation(
                        "printf backend-ok",
                        projected_root,
                        env={
                            "AI_IDE_AGENT_SESSION_ID": "agent-1234",
                            "AI_IDE_STRICT_BACKEND": "evil",
                            "AI_IDE_BOUNDARY_SCOPE": "external-override-attempt",
                            "HOME": "C:/Users/Public",
                            "PATH": "C:/Users/Public",
                            "NoDefaultCurrentDirectoryInExePath": "0",
                        },
                    )

        assert invocation is not None
        self.assertIn("AI_IDE_AGENT_SESSION_ID", invocation.command)
        self.assertIn("agent-1234", invocation.command)
        self.assertIn("AI_IDE_STRICT_BACKEND", invocation.command)
        self.assertIn("restricted-host-helper", invocation.command)
        self.assertIn("AI_IDE_BOUNDARY_SCOPE", invocation.command)
        self.assertIn("workspace-only", invocation.command)
        self.assertIn("HOME", invocation.command)
        self.assertNotIn("evil", invocation.command)
        self.assertNotIn("C:/Users/Public", invocation.command)
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", invocation.command)
        self.assertNotIn("external-override-attempt", invocation.command)

    def test_windows_helper_invocation_preserves_internal_blocked_read_roots_overlay(self) -> None:
        adapter = get_platform_adapter("Windows")
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir) / "projection"
            projected_root.mkdir()

            with patch.object(
                adapter,
                "strict_probe",
                return_value=StrictSandboxProbe("windows", True, "restricted-host-helper", "ready", "ready"),
            ):
                with patch.dict(
                    "os.environ",
                    {WINDOWS_STRICT_HELPER_ENV: "C:/tools/ai-ide-restricted-host-helper.exe"},
                ):
                    invocation = adapter.strict_backend_invocation(
                        "printf backend-ok",
                        projected_root,
                        env={"AI_IDE_BLOCKED_READ_ROOTS": "C:/blocked-one;C:/blocked-two"},
                    )

        assert invocation is not None
        self.assertIn("AI_IDE_BLOCKED_READ_ROOTS", invocation.command)
        self.assertIn("C:/blocked-one;C:/blocked-two", invocation.command)

    def test_windows_strict_backend_invocation_supports_helper_command_prefix(self) -> None:
        adapter = get_platform_adapter("Windows")
        helper_script = Path(__file__).resolve().parents[1] / "backend" / "windows" / "windows_restricted_host_helper_stub.py"
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir) / "projection"
            projected_root.mkdir()

            with patch.object(
                adapter,
                "strict_probe",
                return_value=StrictSandboxProbe("windows", True, "restricted-host-helper", "ready", "ready"),
            ):
                with patch.dict(
                    "os.environ",
                    {WINDOWS_STRICT_HELPER_ENV: f'{sys.executable} {helper_script}'},
                ):
                    invocation = adapter.strict_backend_invocation("printf backend-ok", projected_root)

        self.assertIsNotNone(invocation)
        assert invocation is not None
        self.assertEqual(sys.executable, invocation.command[0])
        self.assertEqual(str(helper_script), invocation.command[1])

    def test_windows_strict_backend_invocation_supports_rust_dev_helper_token(self) -> None:
        adapter = get_platform_adapter("Windows")
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir) / "projection"
            projected_root.mkdir()

            with patch.object(
                adapter,
                "strict_probe",
                return_value=StrictSandboxProbe("windows", True, "restricted-host-helper", "ready", "ready"),
            ):
                with patch.dict("os.environ", {WINDOWS_STRICT_HELPER_ENV: WINDOWS_STRICT_HELPER_RUST_DEV}):
                    with patch(
                        "backend.windows.windows_strict_helper_resolution.shutil.which",
                        side_effect=lambda name: "C:/Users/test/.cargo/bin/cargo.exe" if name == "cargo" else None,
                    ):
                        invocation = adapter.strict_backend_invocation("printf backend-ok", projected_root)

        self.assertIsNotNone(invocation)
        assert invocation is not None
        self.assertEqual("C:/Users/test/.cargo/bin/cargo.exe", invocation.command[0])
        self.assertEqual("run", invocation.command[1])
        self.assertIn("ai-ide-windows-helper", invocation.command)
        self.assertEqual("printf backend-ok", invocation.command[-1])

    def test_windows_strict_backend_invocation_uses_stdio_proxy_in_process_mode(self) -> None:
        adapter = get_platform_adapter("Windows")
        helper_script = Path(__file__).resolve().parents[1] / "backend" / "windows" / "windows_restricted_host_helper_stub.py"
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir) / "projection"
            projected_root.mkdir()

            with patch.object(
                adapter,
                "strict_probe",
                return_value=StrictSandboxProbe("windows", True, "restricted-host-helper", "ready", "ready"),
            ):
                with patch.dict(
                    "os.environ",
                    {WINDOWS_STRICT_HELPER_ENV: f'{sys.executable} {helper_script}'},
                ):
                    invocation = adapter.strict_backend_invocation(
                        "python -u -V",
                        projected_root,
                        process_mode=True,
                        argv=["python", "-u", "-V"],
                        control_file=projected_root / ".control" / "stop.txt",
                        response_file=projected_root / ".control" / "status.json",
                    )

        self.assertIsNotNone(invocation)
        assert invocation is not None
        self.assertIn(STDIO_PROXY_FLAG, invocation.command)
        self.assertIn("--control-file", invocation.command)
        self.assertIn("--response-file", invocation.command)
        self.assertTrue(any(token.startswith("--argv=") for token in invocation.command))
        self.assertEqual("--argv=-V", invocation.command[-1])

    def test_linux_strict_backend_invocation_uses_bwrap_workspace_root(self) -> None:
        adapter = get_platform_adapter("Linux")
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir) / "projection"
            projected_root.mkdir()

            with patch("shutil.which", side_effect=lambda name: "/usr/bin/bwrap" if name == "bwrap" else None):
                with patch(
                    "backend.windows.platforms._existing_system_bind_paths",
                    return_value=["/usr", "/bin", "/lib64"],
                ):
                    invocation = adapter.strict_backend_invocation("printf backend-ok", projected_root)

        self.assertIsNotNone(invocation)
        assert invocation is not None
        self.assertEqual("bwrap", invocation.backend)
        self.assertEqual(projected_root, invocation.host_working_directory)
        self.assertEqual("/workspace", invocation.working_directory)
        self.assertEqual("bwrap", invocation.command[0])
        self.assertEqual("--bind", invocation.command[3])
        self.assertIn(str(projected_root), invocation.command)
        self.assertIn("--ro-bind", invocation.command)
        self.assertIn("/usr", invocation.command)
        self.assertIn("/bin", invocation.command)
        self.assertIn("/lib64", invocation.command)
        self.assertIn("--unshare-net", invocation.command)
        self.assertIn("--clearenv", invocation.command)
        self.assertIn("--die-with-parent", invocation.command)
        self.assertIn("--tmpfs", invocation.command)
        self.assertIn("/tmp/ai-ide-home", invocation.command)
        self.assertIn("/tmp/ai-ide-cache", invocation.command)
        self.assertEqual("AI_IDE_RUNNER_MODE", invocation.command[invocation.command.index("--setenv") + 1])
        self.assertIn("AI_IDE_STRICT_BACKEND", invocation.command)
        self.assertIn("AI_IDE_SANDBOX_ROOT", invocation.command)
        self.assertIn("HOME", invocation.command)
        self.assertIn("XDG_CACHE_HOME", invocation.command)
        self.assertEqual("sh", invocation.command[-3])
        self.assertEqual("-lc", invocation.command[-2])
        self.assertEqual("printf backend-ok", invocation.command[-1])

    def test_linux_strict_backend_invocation_passes_env_overlay(self) -> None:
        adapter = get_platform_adapter("Linux")
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir) / "projection"
            projected_root.mkdir()

            with patch("shutil.which", side_effect=lambda name: "/usr/bin/bwrap" if name == "bwrap" else None):
                with patch(
                    "backend.windows.platforms._existing_system_bind_paths",
                    return_value=["/usr"],
                ):
                    invocation = adapter.strict_backend_invocation(
                        "printf backend-ok",
                        projected_root,
                        env={"AI_IDE_AGENT_SESSION_ID": "agent-1234"},
                    )

        self.assertIsNotNone(invocation)
        assert invocation is not None
        self.assertIn("AI_IDE_AGENT_SESSION_ID", invocation.command)
        index = invocation.command.index("AI_IDE_AGENT_SESSION_ID")
        self.assertEqual("agent-1234", invocation.command[index + 1])

    def test_linux_strict_backend_invocation_ignores_reserved_env_overrides(self) -> None:
        adapter = get_platform_adapter("Linux")
        with tempfile.TemporaryDirectory() as temp_dir:
            projected_root = Path(temp_dir) / "projection"
            projected_root.mkdir()

            with patch("shutil.which", side_effect=lambda name: "/usr/bin/bwrap" if name == "bwrap" else None):
                with patch(
                    "backend.windows.platforms._existing_system_bind_paths",
                    return_value=["/usr"],
                ):
                    invocation = adapter.strict_backend_invocation(
                        "printf backend-ok",
                        projected_root,
                        env={
                            "AI_IDE_AGENT_SESSION_ID": "agent-1234",
                            "AI_IDE_STRICT_BACKEND": "evil",
                            "HOME": "/tmp/evil-home",
                            "PATH": "/tmp/evil-bin",
                            "NoDefaultCurrentDirectoryInExePath": "0",
                        },
                    )

        assert invocation is not None
        self.assertIn("AI_IDE_AGENT_SESSION_ID", invocation.command)
        self.assertNotIn("evil", invocation.command)
        self.assertNotIn("/tmp/evil-home", invocation.command)
        self.assertNotIn("/tmp/evil-bin", invocation.command)
        self.assertNotIn("NoDefaultCurrentDirectoryInExePath", invocation.command)


if __name__ == "__main__":
    unittest.main()
