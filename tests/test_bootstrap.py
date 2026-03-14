from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_ide.bootstrap import (
    RUST_HOST_BIN_ENV,
    RUST_HOST_DEFAULT_AGENT_KIND_ENV,
    RUST_HOST_ENABLE_ENV,
    RUST_HOST_POLICY_STORE_ENV,
    RUST_HOST_REVIEW_STORE_ENV,
    RUST_HOST_BROKER_STORE_ENV,
    RUST_HOST_WORKSPACE_INDEX_STORE_ENV,
    build_optional_rust_host_client,
    create_app,
    resolve_rust_host_settings,
    resolve_runtime_root,
)
from ai_ide.internal import INTERNAL_PROJECT_METADATA_DIR_NAME, INTERNAL_POLICY_STATE_FILENAME


class BootstrapTests(unittest.TestCase):
    def test_resolve_runtime_root_prefers_explicit_value(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            runtime_root.mkdir()

            resolved = resolve_runtime_root(root, runtime_root)

            self.assertEqual(runtime_root.resolve(), resolved)

    def test_resolve_rust_host_settings_is_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            runtime_root.mkdir()

            settings = resolve_rust_host_settings(root, runtime_root=runtime_root, env={})

            self.assertIsNone(settings)

    def test_resolve_rust_host_settings_uses_shared_default_store_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            runtime_root.mkdir()

            settings = resolve_rust_host_settings(
                root,
                runtime_root=runtime_root,
                env={RUST_HOST_ENABLE_ENV: "1"},
            )

            self.assertIsNotNone(settings)
            assert settings is not None
            self.assertEqual("default", settings.default_agent_kind)
            self.assertEqual(
                (root / INTERNAL_PROJECT_METADATA_DIR_NAME / INTERNAL_POLICY_STATE_FILENAME).resolve(),
                settings.policy_store_path,
            )
            self.assertEqual(
                (runtime_root / "reviews" / "state.json").resolve(),
                settings.review_store_path,
            )
            self.assertEqual(
                (runtime_root / "workspace" / "index.json").resolve(),
                settings.workspace_index_store_path,
            )
            self.assertEqual(
                (runtime_root / "broker" / "state.json").resolve(),
                settings.broker_store_path,
            )
            self.assertIsNone(settings.base_command)

    def test_build_optional_rust_host_client_honors_env_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            runtime_root.mkdir()

            client = build_optional_rust_host_client(
                root,
                runtime_root=runtime_root,
                env={
                    RUST_HOST_ENABLE_ENV: "true",
                    RUST_HOST_BIN_ENV: str(base / "bin" / "ai-ide-adapter"),
                    RUST_HOST_DEFAULT_AGENT_KIND_ENV: "codex",
                    RUST_HOST_POLICY_STORE_ENV: str(base / "policy.json"),
                    RUST_HOST_REVIEW_STORE_ENV: str(base / "reviews.json"),
                    RUST_HOST_WORKSPACE_INDEX_STORE_ENV: str(base / "workspace-index.json"),
                    RUST_HOST_BROKER_STORE_ENV: str(base / "broker-state.json"),
                },
            )

            self.assertIsNotNone(client)
            assert client is not None
            self.assertEqual("codex", client.default_agent_kind)
            self.assertEqual((str((base / "bin" / "ai-ide-adapter").resolve()),), tuple(client.base_command))
            self.assertEqual((base / "policy.json").resolve(), client.policy_store_path)
            self.assertEqual((base / "reviews.json").resolve(), client.review_store_path)
            self.assertEqual((base / "workspace-index.json").resolve(), client.workspace_index_store_path)
            self.assertEqual((base / "broker-state.json").resolve(), client.broker_store_path)

    def test_create_app_can_inject_rust_host_client_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            runtime_root.mkdir()

            app = create_app(
                root,
                runtime_root=runtime_root,
                env={RUST_HOST_ENABLE_ENV: "1"},
            )
            try:
                self.assertEqual(runtime_root.resolve(), app.runtime_root)
                self.assertIsNotNone(app.rust_control)
                assert app.rust_control is not None
                self.assertEqual(
                    (runtime_root / "reviews" / "state.json").resolve(),
                    app.rust_control.client.review_store_path,
                )
                self.assertEqual(
                    (runtime_root / "workspace" / "index.json").resolve(),
                    app.rust_control.client.workspace_index_store_path,
                )
                self.assertEqual(
                    (runtime_root / "broker" / "state.json").resolve(),
                    app.rust_control.client.broker_store_path,
                )
            finally:
                app.close()

    def test_create_app_leaves_rust_host_disabled_without_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            runtime_root.mkdir()

            app = create_app(root, runtime_root=runtime_root, env={})
            try:
                self.assertIsNone(app.rust_control)
            finally:
                app.close()


if __name__ == "__main__":
    unittest.main()
