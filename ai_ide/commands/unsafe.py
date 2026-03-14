from __future__ import annotations

from typing import TYPE_CHECKING

from ai_ide.command_access_service import CommandAccessService, CommandContext

if TYPE_CHECKING:
    from ai_ide.app import AIIdeApp


def handle_unsafe_command(
    app: "AIIdeApp",
    parts: list[str],
    raw: str,
    context: CommandContext,
) -> str:
    if len(parts) < 2:
        raise ValueError("Usage: unsafe write <file> <text>")

    subcommand = parts[1]
    if subcommand == "write" and len(parts) >= 4:
        path = parts[2]
        text = raw.split(path, 1)[1].strip()
        try:
            CommandAccessService.require_trusted(context, "unsafe.write")
        except PermissionError as exc:
            app.broker.record_runtime_action(
                "unsafe.write",
                path,
                allowed=False,
                detail=str(exc),
                count_as_write=True,
            )
            raise
        app.broker.write_file(path, text, action="unsafe.write")
        app.sync_workspace_index_cache()
        app.note_workspace_visibility_change("unsafe.write", target=path)
        return f"written unsafe=true target={path}"

    raise ValueError("Usage: unsafe write <file> <text>")
