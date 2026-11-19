from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from components.canvas.canvas_state_manager import CanvasStateManager
from components.theater_manager import Theater, TheaterManager
from providers.decision_provider import (
    ChoiceAnswer,
    ChoiceQuestion,
    DecisionProvider,
    DecisionProviderError,
    DecisionRequest,
    DecisionResult,
)
from testing.base import BaseTestCase
from tools.chat_tool import ChatTools


def _create_avatar_theater(
    root: Path,
    theater_id: str,
    *,
    enabled: bool,
    package: str = "narratron",
    manifest: dict[str, str | list[dict[str, str | bool]]] | None = None,
) -> Theater:
    theater_dir = root / theater_id
    theater_dir.mkdir(parents=True, exist_ok=True)
    yaml_text = f"avatar:\n  enabled: {'true' if enabled else 'false'}\n  package: {package}\n"
    (theater_dir / "theater.yaml").write_text(yaml_text, encoding="utf-8")
    if manifest is not None:
        avatar_dir = theater_dir / "avatars" / package
        avatar_dir.mkdir(parents=True, exist_ok=True)
        (avatar_dir / "avatar.json").write_text(json.dumps(manifest), encoding="utf-8")
    return TheaterManager(str(root)).theater(theater_id)


class TestChatTools(BaseTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.theater = MagicMock(theater_id="test_theater")
        self.theater.config = MagicMock(return_value={})
        self.canvas_manager = MagicMock()
        self.canvas_manager.avatar.enabled = False
        self.decision_provider = MagicMock(spec=DecisionProvider)
        self.chat_tools = ChatTools(
            self.theater,
            self.canvas_manager,
            decision_provider=self.decision_provider,
        )

    def test_requires_decision_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "decision_provider is required"):
            ChatTools(self.theater, self.canvas_manager, decision_provider=None)  # type: ignore[arg-type]

    def test_send_chat_message_success(self) -> None:
        self.chat_tools.canvas_manager = MagicMock()
        self.chat_tools.canvas_manager.avatar.enabled = False
        mock_cb = MagicMock()
        self.chat_tools.on_send_chat_message = mock_cb

        res = self.chat_tools.send_chat_message("Hello traveler!")
        self.assertIn("Successfully updated the Narratron thought panel: Hello traveler!", res)
        self.chat_tools.canvas_manager.tool_response.set_agent_thought.assert_called_once_with("Hello traveler!")
        mock_cb.assert_called_once_with("Hello traveler!")
        self.decision_provider.decide.assert_not_called()

    def test_send_chat_message_no_callback(self) -> None:
        res = self.chat_tools.send_chat_message("Welcome!")
        self.assertIn("Successfully updated the Narratron thought panel: Welcome!", res)
        self.decision_provider.decide.assert_not_called()

    def test_send_chat_message_callback_exception(self) -> None:
        def failing_cb(text: str) -> None:
            raise RuntimeError("Connection broken")

        self.chat_tools.on_send_chat_message = failing_cb
        res = self.chat_tools.send_chat_message("Test message")
        self.assertIn("Error sending chat message: Connection broken", res)
        self.decision_provider.decide.assert_not_called()

    def test_send_chat_message_cycle_cooldown_schedules_and_updates(self) -> None:
        self.chat_tools.cooldown_duration = 10.0
        res1 = self.chat_tools.send_chat_message("Thought 1")
        self.assertIn("Successfully updated the Narratron thought panel: Thought 1", res1)

        res2 = self.chat_tools.send_chat_message("Thought 2")
        self.assertEqual(res2, "Tool 'send_chat_message' scheduled for next cycle when cooldown expires.")

        res3 = self.chat_tools.send_chat_message("Thought 3")
        self.assertEqual(res3, "Tool 'send_chat_message' parameters updated for next cycle.")

        pending = self.chat_tools.get_pending_cycle_call("send_chat_message")
        self.assertIsNotNone(pending)
        self.assertEqual(pending["args"], ("Thought 3",))
        self.chat_tools.cancel_pending_cycle_call("send_chat_message")

    def test_send_chat_message_cycle_cooldown_executes_on_expiry(self) -> None:
        self.chat_tools.cooldown_duration = 0.05
        res1 = self.chat_tools.send_chat_message("First thought")
        self.assertIn("Successfully updated the Narratron thought panel: First thought", res1)

        res2 = self.chat_tools.send_chat_message("Second thought")
        self.assertEqual(res2, "Tool 'send_chat_message' scheduled for next cycle when cooldown expires.")

        deadline = time.time() + 2.0
        while time.time() < deadline and self.chat_tools.canvas_manager.tool_response.set_agent_thought.call_count < 2:
            time.sleep(0.01)
        self.assertEqual(self.chat_tools.canvas_manager.tool_response.set_agent_thought.call_count, 2)
        self.chat_tools.canvas_manager.tool_response.set_agent_thought.assert_called_with("Second thought")

    def test_send_chat_message_selects_avatar_action_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            theater = _create_avatar_theater(
                Path(tmp),
                "narratron_stage",
                enabled=True,
                package="narratron",
                manifest={
                    "id": "narratron",
                    "title": "Narratron",
                    "idle": "idle",
                    "actions": [
                        {
                            "id": "idle",
                            "label": "Idle",
                            "clip": "Idle",
                            "loop": True,
                            "lightColor": "#7ce8ff",
                            "description": "Serene cosmic float.",
                        },
                        {
                            "id": "greeting",
                            "label": "Greeting",
                            "clip": "Greeting",
                            "loop": False,
                            "lightColor": "#ffd27d",
                            "description": "Welcoming bow and wave.",
                        },
                        {
                            "id": "amused",
                            "label": "Amused",
                            "clip": "Amused",
                            "loop": False,
                            "lightColor": "#e29cff",
                            "description": "Playful chuckle bounce.",
                        },
                    ],
                },
            )
            canvas = CanvasStateManager(theater)
            decision_provider = MagicMock(spec=DecisionProvider)
            decision_provider.decide.return_value = DecisionResult(
                provider="test",
                model="test-model",
                answers={"avatar_action": ChoiceAnswer(name="avatar_action", choice="greeting")},
            )
            chat_tools = ChatTools(theater, canvas, decision_provider=decision_provider)

            res = chat_tools.send_chat_message("Welcome, traveler! Step into the lantern light.")

            self.assertIn("Successfully updated the Narratron thought panel:", res)
            decision_provider.decide.assert_called_once()
            request: DecisionRequest = decision_provider.decide.call_args.args[0]
            self.assertIn("Welcome, traveler! Step into the lantern light.", request.input_text)
            self.assertIn("Narratron", request.input_text)
            self.assertIn("Welcoming bow and wave.", request.input_text)
            self.assertIn("Playful chuckle bounce.", request.input_text)
            self.assertEqual(len(request.questions), 1)
            question = request.questions[0]
            self.assertEqual(question.name, "avatar_action")
            self.assertEqual(question.question_type, "choice")
            self.assertEqual(
                ChoiceQuestion(
                    name=question.name,
                    instructions=question.instructions,
                    choices=("idle", "greeting", "amused"),
                ).to_wire_dict(),
                question.to_wire_dict(),
            )
            self.assertEqual(canvas.avatar.current_action, "greeting")
            self.assertEqual(canvas.avatar.action_sequence, 1)
            self.assertEqual(canvas.get_latest_state()["avatar"]["action"], "greeting")
            self.assertEqual(canvas.get_latest_state()["avatar"]["sequence"], 1)

    def test_send_chat_message_ignores_decision_provider_when_avatar_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            theater = _create_avatar_theater(
                Path(tmp),
                "disabled_stage",
                enabled=False,
                package="narratron",
                manifest={
                    "id": "narratron",
                    "title": "Narratron",
                    "idle": "idle",
                    "actions": [
                        {"id": "idle", "label": "Idle", "loop": True},
                        {"id": "greeting", "label": "Greeting", "loop": False},
                    ],
                },
            )
            canvas = CanvasStateManager(theater)
            decision_provider = MagicMock(spec=DecisionProvider)
            chat_tools = ChatTools(theater, canvas, decision_provider=decision_provider)

            res = chat_tools.send_chat_message("Hello!")
            self.assertIn("Successfully updated the Narratron thought panel: Hello!", res)
            decision_provider.decide.assert_not_called()
            self.assertIsNone(canvas.avatar.current_action)
            self.assertEqual(canvas.avatar.action_sequence, 0)

    def test_select_avatar_action_supports_color_only_actions_and_single_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            theater = _create_avatar_theater(
                Path(tmp),
                "cube_stage",
                enabled=True,
                package="light-cube",
                manifest={
                    "id": "light-cube",
                    "title": "Cube of Light",
                    "idle": "idle",
                    "actions": [
                        {"id": "idle", "label": "Gold", "loop": True, "lightColor": "#ffc477"},
                        {"id": "ember", "label": "Ember", "lightColor": "#ff5940"},
                    ],
                },
            )
            canvas = CanvasStateManager(theater)
            decision_provider = MagicMock(spec=DecisionProvider)
            decision_provider.decide.return_value = DecisionResult(
                provider="test",
                model="test-model",
                answers={"avatar_action": ChoiceAnswer(name="avatar_action", choice="ember")},
            )
            chat_tools = ChatTools(theater, canvas, decision_provider=decision_provider)

            selected = chat_tools.select_avatar_action("Danger burns across the chamber!")
            self.assertEqual(selected, "ember")
            request: DecisionRequest = decision_provider.decide.call_args.args[0]
            self.assertIn("color #ff5940", request.input_text)
            self.assertIn("Gold", request.input_text)

            # Empty text skips decision provider
            decision_provider.reset_mock()
            self.assertIsNone(chat_tools.select_avatar_action("   "))
            decision_provider.decide.assert_not_called()

        with tempfile.TemporaryDirectory() as tmp:
            single_theater = _create_avatar_theater(
                Path(tmp),
                "single_action_stage",
                enabled=True,
                package="solo",
                manifest={
                    "id": "solo",
                    "title": "Solo",
                    "idle": "idle",
                    "actions": [{"id": "idle", "label": "Idle", "loop": True}],
                },
            )
            single_canvas = CanvasStateManager(single_theater)
            single_decision = MagicMock(spec=DecisionProvider)
            single_chat = ChatTools(single_theater, single_canvas, decision_provider=single_decision)
            self.assertEqual(single_chat.select_avatar_action("Hello"), "idle")
            single_decision.decide.assert_not_called()
            self.assertEqual(single_canvas.avatar.current_action, "idle")

    def test_select_avatar_action_escalates_decision_provider_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            theater = _create_avatar_theater(
                Path(tmp),
                "error_stage",
                enabled=True,
                package="narratron",
                manifest={
                    "id": "narratron",
                    "title": "Narratron",
                    "idle": "idle",
                    "actions": [
                        {"id": "idle", "label": "Idle", "loop": True},
                        {"id": "surprised", "label": "Surprised", "loop": False},
                    ],
                },
            )
            canvas = CanvasStateManager(theater)
            decision_provider = MagicMock(spec=DecisionProvider)
            decision_provider.decide.side_effect = DecisionProviderError("Decisions API unavailable")
            chat_tools = ChatTools(theater, canvas, decision_provider=decision_provider)

            with self.assertRaisesRegex(DecisionProviderError, "Decisions API unavailable"):
                chat_tools.select_avatar_action("Whoa!")


if __name__ == "__main__":
    unittest.main()
