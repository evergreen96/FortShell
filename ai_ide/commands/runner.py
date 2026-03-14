from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_ide.app import AIIdeApp


def handle_runner_command(app: "AIIdeApp", parts: list[str], raw: str) -> str:
    if len(parts) < 2:
        raise ValueError("Usage: runner show|info|probe|validate|mode|refresh|exec ...")

    subcommand = parts[1]
    if subcommand == "show":
        return app.runners.status()

    if subcommand == "info":
        return app.runners.backend_status_json()

    if subcommand == "probe":
        return app.runners.probe()

    if subcommand == "validate":
        report = app.runners.validate_strict_backend()
        if len(parts) >= 3 and parts[2] == "json":
            return report.to_json()
        return (
            f"status={report.status} backend={report.backend} ready={str(report.ready).lower()} "
            f"reason={report.reason}"
        )

    if subcommand == "mode" and len(parts) >= 3:
        mode = app.runners.set_mode(parts[2])
        return f"runner mode={mode}"

    if subcommand == "refresh":
        manifest = app.runners.refresh_projection()
        app.sync_workspace_index_cache()
        return (
            f"projection session={manifest.session_id} root={manifest.root} "
            f"files={manifest.file_count} dirs={manifest.directory_count}"
        )

    if subcommand == "exec" and len(parts) >= 3:
        command_text = raw.split("exec", 1)[1].strip()
        result = app.runners.run(command_text)
        output = (result.stdout + result.stderr).strip() or "(no output)"
        return (
            f"[mode={result.mode} backend={result.backend} "
            f"cwd={result.working_directory} code={result.returncode}]\n{output}"
        )

    raise ValueError(
        "Usage: runner show | runner info | runner probe | runner validate [json] | "
        "runner mode <host|projected|strict> | runner refresh | runner exec <command>"
    )
