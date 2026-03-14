from __future__ import annotations

from pathlib import Path

from backend.bootstrap import create_app
from backend.command_access_service import CommandContext


def main() -> None:
    app = create_app(Path.cwd())
    print("AI IDE prototype started. Type 'help' for commands.")
    try:
        while True:
            try:
                line = input("ai-ide> ")
            except (KeyboardInterrupt, EOFError):
                print("\nbye")
                break

            try:
                output = app.handle_command(line, context=CommandContext.user())
                if output:
                    print(output)
            except SystemExit:
                print("bye")
                break
            except Exception as exc:  # pylint: disable=broad-except
                print(f"error: {exc}")
    finally:
        app.close()
