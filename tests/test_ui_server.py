from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from backend.ui_server import serve_workspace_panel


class UiServerTests(unittest.TestCase):
    def test_root_serves_static_index(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()

            server = serve_workspace_panel(root, runtime_root=runtime_root, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_address[1]}"
                with urlopen(f"{base_url}/") as response:
                    body = response.read().decode("utf-8")

                self.assertIn("AI IDE Workspace Panel", body)
                self.assertIn("Visible workspace tree and AI access policy controls.", body)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_workspace_panel_api_returns_visible_tree_and_policy_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible plan", encoding="utf-8")

            server = serve_workspace_panel(root, runtime_root=runtime_root, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_address[1]}"
                with urlopen(f"{base_url}/api/workspace-panel?target=.") as response:
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual("workspace_panel", payload["kind"])
                self.assertEqual(["notes", "notes/todo.txt"], [entry["path"] for entry in payload["workspace"]["entries"]])
                self.assertEqual([], payload["policy"]["deny_globs"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_desktop_shell_api_returns_workspace_and_terminals_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible plan", encoding="utf-8")

            server = serve_workspace_panel(root, runtime_root=runtime_root, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                server.app.handle_command("term new managed-shell-1")
                base_url = f"http://127.0.0.1:{server.server_address[1]}"
                with urlopen(f"{base_url}/api/desktop-shell?target=.") as response:
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual("desktop_shell", payload["kind"])
                self.assertEqual({"kind", "target", "workspace_panel", "terminals", "terminal_profiles"}, set(payload))
                self.assertEqual("workspace_panel", payload["workspace_panel"]["kind"])
                self.assertEqual(1, payload["terminals"]["count"])
                self.assertEqual("managed-shell-1", payload["terminals"]["items"][0]["name"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_policy_deny_and_allow_endpoints_mutate_panel_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            (root / "notes" / "todo.txt").write_text("visible plan", encoding="utf-8")

            server = serve_workspace_panel(root, runtime_root=runtime_root, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_address[1]}"

                denied = self._post_json(
                    f"{base_url}/api/policy/deny",
                    {"rule": "notes/**", "target": "."},
                )
                allowed = self._post_json(
                    f"{base_url}/api/policy/allow",
                    {"rule": "notes/**", "target": "."},
                )

                self.assertEqual(["notes/**"], denied["panel"]["policy"]["deny_globs"])
                self.assertEqual([], denied["panel"]["workspace"]["entries"])
                self.assertEqual([], allowed["panel"]["policy"]["deny_globs"])
                self.assertEqual(
                    ["notes", "notes/todo.txt"],
                    [entry["path"] for entry in allowed["panel"]["workspace"]["entries"]],
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_policy_endpoints_reject_non_workspace_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()

            server = serve_workspace_panel(root, runtime_root=runtime_root, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_address[1]}"
                with self.assertRaises(HTTPError) as context:
                    self._post_json(
                        f"{base_url}/api/policy/deny",
                        {"rule": "notes/**", "target": "../outside-panel"},
                    )
                self.assertEqual(403, context.exception.code)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_policy_endpoint_rejects_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()

            server = serve_workspace_panel(root, runtime_root=runtime_root, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_address[1]}"
                request = Request(
                    f"{base_url}/api/policy/deny",
                    data=b"not-json",
                    method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(HTTPError) as context:
                    urlopen(request)
                self.assertEqual(400, context.exception.code)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_terminal_create_and_run_endpoints_return_terminal_snapshot_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()

            server = serve_workspace_panel(root, runtime_root=runtime_root, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_address[1]}"
                created = self._post_json(
                    f"{base_url}/api/terminal/create",
                    {
                        "name": "managed-shell-1",
                        "transport": "host",
                    },
                )
                run = self._post_json(
                    f"{base_url}/api/terminal/run",
                    {
                        "terminal_id": created["terminal"]["terminal_id"],
                        "command": "echo hello",
                    },
                )

                self.assertEqual("terminal_create", created["kind"])
                self.assertEqual("managed-shell-1", created["terminal"]["name"])
                self.assertEqual("terminal_run", run["kind"])
                self.assertIn("hello", run["output"].lower())
                self.assertEqual(created["terminal"]["terminal_id"], run["terminal"]["terminal_id"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_editor_file_and_stage_apply_reject_endpoints_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "src").mkdir()
            target = root / "src" / "app.py"
            target.write_text("print('old')\n", encoding="utf-8")

            server = serve_workspace_panel(root, runtime_root=runtime_root, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_address[1]}"

                with urlopen(f"{base_url}/api/editor/file?target=src/app.py") as response:
                    file_payload = json.loads(response.read().decode("utf-8"))

                staged = self._post_json(
                    f"{base_url}/api/editor/stage",
                    {"target": "src/app.py", "content": "print('new')\n"},
                )
                rejected = self._post_json(
                    f"{base_url}/api/editor/reject",
                    {"proposal_id": staged["proposal"]["proposal_id"]},
                )

                staged_again = self._post_json(
                    f"{base_url}/api/editor/stage",
                    {"target": "src/app.py", "content": "print('applied')\n"},
                )
                applied = self._post_json(
                    f"{base_url}/api/editor/apply",
                    {"proposal_id": staged_again["proposal"]["proposal_id"]},
                )

                self.assertEqual("editor_file", file_payload["kind"])
                self.assertEqual("src/app.py", file_payload["path"])
                self.assertIn("print('old')", file_payload["content"])
                self.assertIsNone(file_payload["proposal"])
                self.assertIsNone(file_payload["rendered"])
                self.assertEqual("editor_stage", staged["kind"])
                self.assertIn("--- a/src/app.py", staged["rendered"])
                self.assertEqual("editor_reject", rejected["kind"])
                self.assertEqual("rejected", rejected["proposal"]["status"])
                self.assertEqual("editor_apply", applied["kind"])
                self.assertEqual("applied", applied["proposal"]["status"])
                self.assertEqual("print('applied')", target.read_text(encoding="utf-8").strip())

                with urlopen(f"{base_url}/api/editor/file?target=src/app.py") as response:
                    after_apply = json.loads(response.read().decode("utf-8"))
                self.assertIsNone(after_apply["proposal"])
                self.assertIsNone(after_apply["rendered"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_editor_save_endpoint_writes_directly(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "src").mkdir()
            target = root / "src" / "app.py"
            target.write_text("print('old')\n", encoding="utf-8")

            server = serve_workspace_panel(root, runtime_root=runtime_root, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_address[1]}"
                saved = self._post_json(
                    f"{base_url}/api/editor/save",
                    {"target": "src/app.py", "content": "print('saved')\n"},
                )
                self.assertEqual("editor_save", saved["kind"])
                self.assertEqual("print('saved')\n", target.read_text(encoding="utf-8"))
                self.assertIsNone(saved["proposal"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_editor_file_endpoint_exposes_pending_proposal_for_selected_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "src").mkdir()
            target = root / "src" / "app.py"
            target.write_text("print('old')\n", encoding="utf-8")

            server = serve_workspace_panel(root, runtime_root=runtime_root, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_address[1]}"
                staged = self._post_json(
                    f"{base_url}/api/editor/stage",
                    {"target": "src/app.py", "content": "print('next')\n"},
                )

                with urlopen(f"{base_url}/api/editor/file?target=src/app.py") as response:
                    payload = json.loads(response.read().decode("utf-8"))

                self.assertEqual("editor_file", payload["kind"])
                self.assertEqual(staged["proposal"]["proposal_id"], payload["proposal"]["proposal_id"])
                self.assertIn("--- a/src/app.py", payload["rendered"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_review_render_apply_and_reject_endpoints_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()
            (root / "notes").mkdir()
            target = root / "notes" / "todo.txt"
            target.write_text("old line\n", encoding="utf-8")

            server = serve_workspace_panel(root, runtime_root=runtime_root, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                staged = server.app.handle_command("review stage notes/todo.txt new line")
                proposal_id = staged.split("proposal_id=", 1)[1].split()[0]
                base_url = f"http://127.0.0.1:{server.server_address[1]}"

                with urlopen(f"{base_url}/api/review/render?proposal_id={proposal_id}") as response:
                    rendered = json.loads(response.read().decode("utf-8"))
                rejected = self._post_json(
                    f"{base_url}/api/review/reject",
                    {"proposal_id": proposal_id, "target": "."},
                )

                staged_apply = server.app.handle_command("review stage notes/todo.txt applied line")
                apply_id = staged_apply.split("proposal_id=", 1)[1].split()[0]
                applied = self._post_json(
                    f"{base_url}/api/review/apply",
                    {"proposal_id": apply_id, "target": "."},
                )

                self.assertEqual("review_render", rendered["kind"])
                self.assertIn("--- a/notes/todo.txt", rendered["content"])
                self.assertEqual("review_action", rejected["kind"])
                self.assertEqual("reject", rejected["action"])
                self.assertEqual("rejected", rejected["proposal"]["status"])
                self.assertEqual("review_action", applied["kind"])
                self.assertEqual("apply", applied["action"])
                self.assertEqual("applied", applied["proposal"]["status"])
                self.assertEqual("applied line", target.read_text(encoding="utf-8").strip())
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_api_options_returns_cors_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            root = base / "project"
            runtime_root = base / "runtime"
            root.mkdir()

            server = serve_workspace_panel(root, runtime_root=runtime_root, port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base_url = f"http://127.0.0.1:{server.server_address[1]}"
                request = Request(
                    f"{base_url}/api/workspace-panel",
                    method="OPTIONS",
                )
                with urlopen(request) as response:
                    self.assertEqual("GET, POST, OPTIONS", response.headers["Access-Control-Allow-Methods"])
                    self.assertEqual("*", response.headers["Access-Control-Allow-Origin"])
                    self.assertEqual("Content-Type", response.headers["Access-Control-Allow-Headers"])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def _post_json(self, url: str, payload: dict[str, object]) -> dict[str, object]:
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
