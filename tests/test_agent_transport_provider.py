from __future__ import annotations

import unittest

from ai_ide.agent_transport_provider import PipeOnlyTransportProvider


class PipeOnlyTransportProviderTests(unittest.TestCase):
    def test_pipe_preferred_uses_native_pipe_transport(self) -> None:
        provider = PipeOnlyTransportProvider()

        decision = provider.resolve("pipe")

        self.assertEqual("pipe-only", decision.provider_name)
        self.assertEqual("pipe", decision.resolved_io_mode)
        self.assertEqual("native", decision.transport_status)
        self.assertFalse(decision.supports_pty)

    def test_pty_preferred_degrades_to_pipe(self) -> None:
        provider = PipeOnlyTransportProvider()

        decision = provider.resolve("pty_preferred")

        self.assertEqual("pipe", decision.resolved_io_mode)
        self.assertEqual("degraded", decision.transport_status)
        self.assertIn("no PTY transport", decision.detail)

    def test_pty_required_is_unavailable_without_provider_support(self) -> None:
        provider = PipeOnlyTransportProvider()

        decision = provider.resolve("pty_required")

        self.assertEqual("none", decision.resolved_io_mode)
        self.assertEqual("unavailable", decision.transport_status)
        self.assertTrue(decision.provider_ready)
