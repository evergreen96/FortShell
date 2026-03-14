from __future__ import annotations

import unittest

from ai_ide.command_access_service import CommandAccessService, CommandContext


class CommandAccessServiceTests(unittest.TestCase):
    def test_require_trusted_allows_user_context(self) -> None:
        CommandAccessService.require_trusted(CommandContext.user(), "unsafe.write")

    def test_require_trusted_blocks_agent_context(self) -> None:
        with self.assertRaises(PermissionError):
            CommandAccessService.require_trusted(CommandContext.agent(), "unsafe.write")
