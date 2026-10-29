"""Regression checks for bounded prompt delivery and owned-terminal cleanup."""

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from cli_agent_orchestrator.models.terminal import ProviderExitedInputBlockedError, TerminalStatus
from cli_agent_orchestrator.providers.manager import provider_manager
from cli_agent_orchestrator.services import agent_step, terminal_service


class AgentStepDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfirmed_delivery_fails_after_bounded_recovery(self):
        with (
            patch.object(agent_step, "_PROMPT_PICKUP_GRACE", 0.005),
            patch.object(agent_step, "_COMPLETION_POLL_INTERVAL", 0.001),
            patch.object(agent_step.status_monitor, "get_status", return_value=TerminalStatus.IDLE),
            patch.object(provider_manager, "get_provider", return_value=object()),
            patch.object(agent_step.terminal_service, "redeliver_dropped_message", return_value=False) as resend,
        ):
            with self.assertRaises(agent_step.StepExecutionError) as caught:
                await asyncio.wait_for(
                    agent_step._wait_for_completion("terminal-1", 60, prompt="task"),
                    timeout=1,
                )
        self.assertEqual(caught.exception.kind, "delivery_unconfirmed")
        self.assertEqual(caught.exception.terminal_id, "terminal-1")
        self.assertEqual(resend.call_count, agent_step._PROMPT_REDELIVER_MAX)

    async def test_step_timeout_tears_down_owned_terminal(self):
        teardown = AsyncMock()
        with (
            patch.object(agent_step.launch_guard, "check", Mock()),
            patch.object(agent_step.terminal_service, "create_terminal", AsyncMock(return_value=SimpleNamespace(id="terminal-2"))),
            patch.object(agent_step, "wait_until_status", AsyncMock(return_value=True)),
            patch.object(agent_step.frozen_run_memory, "frozen_memory_for", Mock(return_value=None)),
            patch.object(agent_step.terminal_service, "send_input", Mock()),
            patch.object(agent_step, "_wait_for_completion", AsyncMock(side_effect=agent_step.StepExecutionError("slow", kind="timeout", terminal_id="terminal-2"))),
            patch.object(agent_step, "_best_effort_teardown", teardown),
        ):
            with self.assertRaises(agent_step.StepExecutionError):
                await agent_step.run_agent_step("codex", "feature_developer", "task", teardown=True)
        teardown.assert_awaited_once_with("terminal-2", None)

    async def test_readiness_timeout_tears_down_owned_terminal(self):
        teardown = AsyncMock()
        with (
            patch.object(agent_step.launch_guard, "check", Mock()),
            patch.object(agent_step.terminal_service, "create_terminal", AsyncMock(return_value=SimpleNamespace(id="terminal-3"))),
            patch.object(agent_step, "wait_until_status", AsyncMock(return_value=False)),
            patch.object(agent_step, "_best_effort_teardown", teardown),
        ):
            with self.assertRaises(agent_step.StepExecutionError):
                await agent_step.run_agent_step("codex", "feature_developer", "task", teardown=True)
        teardown.assert_awaited_once_with("terminal-3", None)

    async def test_exited_provider_reports_upstream_error_and_cleans_up(self):
        teardown = AsyncMock()
        with (
            patch.object(agent_step.launch_guard, "check", Mock()),
            patch.object(agent_step.terminal_service, "create_terminal", AsyncMock(return_value=SimpleNamespace(id="terminal-4"))),
            patch.object(agent_step, "wait_until_status", AsyncMock(return_value=True)),
            patch.object(agent_step.frozen_run_memory, "frozen_memory_for", Mock(return_value=None)),
            patch.object(agent_step.terminal_service, "send_input", Mock(side_effect=ProviderExitedInputBlockedError("provider is in ERROR state"))),
            patch.object(agent_step, "_best_effort_teardown", teardown),
        ):
            with self.assertRaises(agent_step.StepExecutionError) as caught:
                await agent_step.run_agent_step("codex", "feature_developer", "task", teardown=True)
        self.assertEqual(caught.exception.kind, "provider_exited")
        self.assertEqual(caught.exception.terminal_id, "terminal-4")
        teardown.assert_awaited_once_with("terminal-4", None)

    async def test_failed_step_teardown_does_not_type_exit_into_pending_composer(self):
        with (
            patch.object(terminal_service, "exit_terminal_cli") as exit_cli,
            patch.object(terminal_service, "delete_terminal") as delete_terminal,
        ):
            await agent_step._best_effort_teardown("terminal-5", None)
        exit_cli.assert_not_called()
        delete_terminal.assert_called_once_with("terminal-5", registry=None)

    def test_exact_codex_paste_chip_gets_only_bare_enter(self):
        CodexProvider = type("CodexProvider", (), {})
        prompt = "x" * 9366
        with (
            patch.object(terminal_service, "_message_in_composer", return_value=False),
            patch.object(terminal_service, "_capture_current_composer_region", return_value="› [Pasted Content 9366 chars]"),
            patch.object(terminal_service.status_monitor, "get_status", return_value=TerminalStatus.IDLE),
            patch.object(terminal_service.status_monitor, "output_generation", return_value=7),
            patch.object(terminal_service, "send_special_key") as send_key,
            patch.object(terminal_service, "dispatch_input") as resend,
        ):
            started, boundary = terminal_service.redeliver_dropped_message_with_boundary(
                "terminal-6", prompt, 1, provider=CodexProvider(),
                full_resend_requires_probe=True, allow_codex_paste_marker=True,
            )
        self.assertFalse(started)
        self.assertEqual(boundary, 7)
        send_key.assert_called_once_with("terminal-6", "Enter")
        resend.assert_not_called()

    def test_edited_codex_paste_chip_is_not_submitted(self):
        CodexProvider = type("CodexProvider", (), {})
        with (
            patch.object(terminal_service, "_message_in_composer", return_value=False),
            patch.object(terminal_service, "_capture_current_composer_region", return_value="› [Pasted Content 9366 chars]/exit"),
            patch.object(terminal_service, "send_special_key") as send_key,
            patch.object(terminal_service, "dispatch_input") as resend,
        ):
            started, boundary = terminal_service.redeliver_dropped_message_with_boundary(
                "terminal-7", "x" * 9366, 1, provider=CodexProvider(),
                full_resend_requires_probe=True, allow_codex_paste_marker=True,
            )
        self.assertFalse(started)
        self.assertIsNone(boundary)
        send_key.assert_not_called()
        resend.assert_not_called()


if __name__ == "__main__":
    unittest.main()
