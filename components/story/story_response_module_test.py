"""Dependency-boundary tests for ``StoryResponseModule``."""

import unittest
import tempfile
from threading import Event
import time
from pathlib import Path
from unittest.mock import MagicMock, patch


from components.canvas.story_state import StoryState
from components.canvas_state import CanvasStateManager
from components.theater_manager import Theater, TheaterManager
from providers import ImageProvider, SpeechProvider, TextResponseProvider
from providers.text_response_provider import TextResponseRequest, TextResponseResult
from providers.decision_provider import (
    ChoiceAnswer,
    DecisionProvider,
    DecisionRequest,
    DecisionResult,
)
from components.reference_manager import Character, ReferenceManager, PlayerCharacter
from components.lore_library import LoreLibrary
from components.notepad import Notepad
from components.story.story_recording_journal import StoryHistoryEvent, StoryRecordingJournal
from components.story.story_response_module import (
    ResponseCharacter,
    ResponseDialogue,
    SceneReaction,
    StoryResponseModule,
    build_responder_turn_prompt,
    build_responder_instructions,
    build_story_context_prompt,
)


class TestStoryResponseModuleDependencies(unittest.TestCase):
    def setUp(self) -> None:
        self.theater_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.theater_directory.cleanup)
        self.theater = MagicMock(
            spec=Theater,
            wraps=TheaterManager(Path(self.theater_directory.name)).theater("test_theater"),
        )
        self.theater.theater_id = "response_boundary"
        self.theater.config.return_value = {"story_planning": {"adventure_mode": False}}
        self.canvas = MagicMock(spec=CanvasStateManager)
        self.provider = MagicMock(spec=TextResponseProvider)
        self.lore_library = MagicMock(spec=LoreLibrary)
        self.reference_manager = MagicMock(spec=ReferenceManager)
        self.provider = MagicMock(spec=TextResponseProvider)
        self.session_id = "response-boundary-session"
        self.notepad = Notepad(self.theater, canvas_manager=self.canvas)
        self.canvas.notepad = self.notepad
        self.decision_provider = MagicMock(spec=DecisionProvider)

    def test_dialogue_supports_a_tts_voice_instruction(self) -> None:
        dialogue = ResponseDialogue(
            speaker="Mara",
            text="The gate is opening.",
            voice_instruction="A hushed warning, growing urgent.",
        )

        self.assertEqual(dialogue.voice_instruction, "A hushed warning, growing urgent.")

    def test_uses_injected_dependencies(self) -> None:
        module = StoryResponseModule(
            self.theater, self.canvas, self.lore_library, self.reference_manager, self.provider,
            self.decision_provider,
        )
        self.assertIs(module.notepad, self.notepad)
        self.assertIs(module.lore_library, self.lore_library)
        self.assertIs(module.reference_manager, self.reference_manager)
        self.assertIs(module.text_response_provider, self.provider)
        self.assertIs(module.decision_provider, self.decision_provider)
        self.provider.generate.assert_not_called()

    def test_requires_text_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "text_response_provider is required"):
            StoryResponseModule(
                self.theater, self.canvas, self.lore_library, self.reference_manager,
                None,  # type: ignore[arg-type]
                self.decision_provider,
            )

    def test_requires_lore_and_references(self) -> None:
        with self.assertRaisesRegex(ValueError, "lore_library is required"):
            StoryResponseModule(
                self.theater, self.canvas, None, self.reference_manager, self.provider,  # type: ignore[arg-type]
                self.decision_provider,
            )
        with self.assertRaisesRegex(ValueError, "reference_manager is required"):
            StoryResponseModule(
                self.theater, self.canvas, self.lore_library, None, self.provider,  # type: ignore[arg-type]
                self.decision_provider,
            )

    def test_requires_decision_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "decision_provider is required"):
            StoryResponseModule(
                self.theater, self.canvas, self.lore_library, self.reference_manager, self.provider,
                None,  # type: ignore[arg-type]
            )
        with self.assertRaises(TypeError):
            StoryResponseModule(  # type: ignore[call-arg]
                self.theater, self.canvas, self.lore_library, self.reference_manager, self.provider,
            )


class TestStoryResponseModuleBehavior(unittest.TestCase):
    def setUp(self) -> None:
        self.theater_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.theater_directory.cleanup)
        self.theater = MagicMock(
            spec=Theater,
            wraps=TheaterManager(Path(self.theater_directory.name)).theater("test_theater"),
        )
        self.theater.theater_id = "response_theater"
        self.theater.lore_documents.return_value = ["lore.txt"]
        self.theater.read_lore_document.return_value = (
            "Lore details about the realm."
        )
        self.config = {
            "adventure_mode": True,
            "notepad": {"initial_stickies": {"HUD": "HP: 100 | MP: 50"}},
        }
        self.theater.config.return_value = self.config

        self.canvas = MagicMock(spec=CanvasStateManager)
        self.story_state = MagicMock(spec=StoryState)
        self.canvas.story = self.story_state
        self.canvas.tool_response = MagicMock()
        self.provider = MagicMock(spec=TextResponseProvider)
        self.lore_library = LoreLibrary(theater=self.theater)
        self.notepad = Notepad(self.theater, canvas_manager=self.canvas)
        self.canvas.notepad = self.notepad
        self.image_provider = MagicMock(spec=ImageProvider)
        self.image_provider.generate.side_effect = RuntimeError("not used")
        self.speech_provider = MagicMock(spec=SpeechProvider)
        self.speech_provider.select_voice.return_value = "voice_default"
        self.reference_manager = ReferenceManager(
            theater=self.theater,
            text_response_provider=self.provider,
            canvas_manager=self.canvas,
            story_state=self.story_state,
            image_provider=self.image_provider,
            speech_provider=self.speech_provider,
        )
        decisions = MagicMock(spec=DecisionProvider)
        decisions.decide.return_value = DecisionResult(provider="test", model="test", answers={
            "primary_lore_document": ChoiceAnswer(name="primary_lore_document", choice="none"),
            "dice_roll": ChoiceAnswer(name="dice_roll", choice="none"),
        })
        self.decisions = decisions
        self.module = StoryResponseModule(
            theater=self.theater,
            canvas_manager=self.canvas,
            lore_library=self.lore_library,
            reference_manager=self.reference_manager,
            text_response_provider=self.provider,
            decision_provider=decisions,
        )

    def test_generates_once_with_fresh_structured_output_and_context(self) -> None:
        self.provider.generate.return_value = TextResponseResult(
            text='', provider='test', model='test', parsed=SceneReaction(narration='The gate opens.'),
        )
        journal = MagicMock(spec=StoryRecordingJournal)
        journal.prompt_context_snapshot.return_value = ('Recent story: A locked gate.', frozenset())
        self.module.recording_journal = journal
        self.module.config['history_event_limit'] = 6
        self.module.config['history_chunk_size'] = 3
        result = self.module._resolve_user_action('I turn the key.', publish=False)
        self.assertEqual(result['narration'], 'The gate opens.')
        self.provider.generate.assert_called_once()
        request = self.provider.generate.call_args.args[0]
        self.assertIs(request.response_schema, SceneReaction)
        self.assertIn('Recent story: A locked gate.', request.prompt)
        journal.prompt_context_snapshot.assert_called_once_with('responder', 6, 3)
        self.assertIn('I turn the key.', request.prompt)
        self.assertIn('# Known Character Profiles', request.prompt)
        self.assertLess(request.prompt.index('# Known Character Profiles'), request.prompt.index('# Current Story State'))
        self.assertIn('No tools are available', request.system_instruction)
        self.provider.generate.return_value = TextResponseResult(text='{}', provider='test', model='test')
        with self.assertRaises(ValueError):
            self.module._resolve_user_action('I wait.', publish=False)
        self.assertEqual(self.provider.generate.call_count, 2)

    def test_can_derive_decisions_from_only_the_text_provider(self) -> None:
        self.module.decision_provider = None
        self.provider.model = "test"
        self.provider.generate.side_effect = [
            TextResponseResult(text="", provider="test", model="test", parsed={"primary_lore_document": "none"}),
            TextResponseResult(text='{"narration":"The door opens."}', provider="test", model="test"),
        ]
        result = self.module._resolve_user_action('I open the door.', publish=False)
        self.assertEqual(result['narration'], 'The door opens.')
        requests = [call.args[0] for call in self.provider.generate.call_args_list]
        self.assertEqual(len(requests), 2)
        self.assertIsNotNone(requests[0].response_json_schema)
        self.assertIs(requests[1].response_schema, SceneReaction)

    def test_generation_failure_is_not_retried_or_published(self) -> None:
        self.provider.generate.side_effect = RuntimeError('provider unavailable')
        with self.assertRaisesRegex(RuntimeError, 'provider unavailable'):
            self.module._resolve_user_action('I wait.', publish=False)
        self.provider.generate.assert_called_once()
        self.story_state.set_scene.assert_not_called()
        self.story_state.record_die_roll.assert_not_called()
        self.assertEqual(self.module.get_die_rolls_this_turn(), [])

    def test_decisions_supply_lore_and_real_dice_before_one_generation(self) -> None:
        decisions = MagicMock(spec=DecisionProvider)
        decisions.decide.side_effect = [
            DecisionResult(provider="test", model="test", answers={
                "primary_lore_document": ChoiceAnswer(name="primary_lore_document", choice="lore.txt"),
            }),
        ]
        self.module.decision_provider = decisions
        self.provider.generate.return_value = TextResponseResult(
            text='{"narration":"The gate opens.","dice_used":true,"dice_reason":"Force the gate"}', provider='test', model='test',
        )
        with patch('random.randint', return_value=17):
            result = self.module._resolve_user_action('I force the gate.', publish=False)
        self.assertEqual(decisions.decide.call_count, 1)
        self.provider.generate.assert_called_once()
        prompt = self.provider.generate.call_args.args[0].prompt
        self.assertIn('Lore details about the realm.', prompt)
        self.assertIn('"rolls": [17]', prompt)
        self.assertEqual(result['die_rolls'][0]['total'], 17)
        self.assertEqual(result['die_rolls'][0]['reason'], 'Force the gate')
        self.assertEqual(result['lore_docs_browsed'], ['lore.txt'])

    def test_unused_provisional_roll_is_not_recorded_or_published(self) -> None:
        self.provider.generate.return_value = TextResponseResult(
            text='{"narration":"The door is already open.","dice_used":false}', provider='test', model='test',
        )
        with patch('random.randint', return_value=1) as randint:
            result = self.module._resolve_user_action('I enter.', publish=False)
        randint.assert_called_once_with(1, 20)
        self.assertIn('"rolls": [1]', self.provider.generate.call_args.args[0].prompt)
        self.assertEqual(result['die_rolls'], [])
        self.story_state.record_die_roll.assert_not_called()
        self.assertFalse(any(call.args[0] == 'dice' for call in self.canvas.tool_response.set_activity.call_args_list))

    def test_provisional_roll_is_published_only_after_validated_use(self) -> None:
        def generate(request: TextResponseRequest) -> TextResponseResult:
            self.assertEqual(self.module.get_die_rolls_this_turn(), [])
            self.story_state.record_die_roll.assert_not_called()
            return TextResponseResult(
                text='{"narration":"A storm approaches.","dice_used":true,"dice_procedural":true}',
                provider='test', model='test',
            )

        self.provider.generate.side_effect = generate
        with patch('random.randint', return_value=8):
            result = self.module._resolve_user_action('I wait.', publish=False)
        self.assertEqual(result['die_rolls'][0]['total'], 8)
        self.assertTrue(result['die_rolls'][0]['procedural'])
        self.story_state.record_die_roll.assert_not_called()

    def test_disabled_dice_never_sample_and_reject_claimed_use(self) -> None:
        self.module.config['enable_dice'] = False
        self.provider.generate.return_value = TextResponseResult(
            text='{"narration":"The door opens."}', provider='test', model='test',
        )
        with patch('random.randint') as randint:
            result = self.module._resolve_user_action('I enter.', publish=False)
        randint.assert_not_called()
        self.assertEqual(result['die_rolls'], [])
        self.provider.generate.return_value = TextResponseResult(
            text='{"narration":"The door opens.","dice_used":true}', provider='test', model='test',
        )
        with self.assertRaisesRegex(ValueError, 'no application roll'):
            self.module._resolve_user_action('I enter.', publish=False)
        self.story_state.record_die_roll.assert_not_called()

    def test_lore_is_reused_until_it_leaves_retained_history(self) -> None:
        journal = StoryRecordingJournal(Path(self.theater_directory.name) / 'history.sqlite')
        self.addCleanup(journal.close)
        self.module.recording_journal = journal
        self.module.config.update(history_event_limit=2, history_chunk_size=1, enable_dice=False)
        decisions = MagicMock(spec=DecisionProvider)
        decisions.decide.return_value = DecisionResult(provider='test', model='test', answers={
            'primary_lore_document': ChoiceAnswer(name='primary_lore_document', choice='lore.txt'),
        })
        self.module.decision_provider = decisions
        self.provider.generate.return_value = TextResponseResult(
            text='{"narration":"The gate opens."}', provider='test', model='test',
        )
        for _ in range(2):
            self.module._resolve_user_action('I inspect the gate.', publish=False)
            prompt = self.provider.generate.call_args.args[0].prompt
            self.assertEqual(prompt.count('Lore details about the realm.'), 1)
        self.theater.read_lore_document.assert_called_once_with('lore.txt')
        self.assertEqual(len(journal.recent_history(kinds=('lore_read',))), 1)
        journal.complete_history([
            StoryHistoryEvent(event_id=f'output-{index}', action_id=f'action-{index}', kind='story_output', payload={'narration': 'Time passes.'})
            for index in range(2)
        ])
        self.module._resolve_user_action('I inspect the gate again.', publish=False)
        self.assertEqual(self.theater.read_lore_document.call_count, 2)
        self.assertEqual(self.provider.generate.call_args.args[0].prompt.count('Lore details about the realm.'), 1)

    def test_new_lore_read_does_not_evict_reused_lore_from_current_prompt(self) -> None:
        journal = StoryRecordingJournal(Path(self.theater_directory.name) / 'history.sqlite')
        self.addCleanup(journal.close)
        self.module.recording_journal = journal
        self.module.config.update(history_event_limit=1, history_chunk_size=1, enable_dice=False)
        journal.record_lore_read('recorder', 'old.txt', 'Lore document: old.txt\n\nEXISTING_LORE')
        self.theater.lore_documents.return_value = ['old.txt', 'new.txt']
        decisions = MagicMock(spec=DecisionProvider)
        decisions.decide.return_value = DecisionResult(provider='test', model='test', answers={
            'primary_lore_document': ChoiceAnswer(name='primary_lore_document', choice='old.txt'),
            'secondary_lore_document': ChoiceAnswer(name='secondary_lore_document', choice='new.txt'),
        })
        self.module.decision_provider = decisions
        self.provider.generate.return_value = TextResponseResult(text='{"narration":"The gate opens."}', provider='test', model='test')
        self.module._resolve_user_action('I inspect both.', publish=False)
        prompt = self.provider.generate.call_args.args[0].prompt
        self.assertEqual(prompt.count('EXISTING_LORE'), 1)
        self.assertEqual(prompt.count('Lore details about the realm.'), 1)
        self.theater.read_lore_document.assert_called_once_with('new.txt')

    def test_disabled_history_reinserts_lore_each_turn_without_duplicates(self) -> None:
        journal = StoryRecordingJournal(Path(self.theater_directory.name) / 'history.sqlite')
        self.addCleanup(journal.close)
        self.module.recording_journal = journal
        self.module.config.update(history_event_limit=0, enable_dice=False)
        decisions = MagicMock(spec=DecisionProvider)
        decisions.decide.return_value = DecisionResult(provider='test', model='test', answers={
            'primary_lore_document': ChoiceAnswer(name='primary_lore_document', choice='lore.txt'),
        })
        self.module.decision_provider = decisions
        self.provider.generate.return_value = TextResponseResult(text='{"narration":"The gate opens."}', provider='test', model='test')
        for _ in range(2):
            self.module._resolve_user_action('I inspect the gate.', publish=False)
            self.assertEqual(self.provider.generate.call_args.args[0].prompt.count('Lore details about the realm.'), 1)
        self.assertEqual(self.theater.read_lore_document.call_count, 2)

    def test_pinned_role_guide_needs_no_lore_selection_or_second_insertion(self) -> None:
        self.theater.lore_documents.return_value = ['RESPONDER.md']
        self.provider.generate.return_value = TextResponseResult(text='{"narration":"The gate opens."}', provider='test', model='test')
        decisions = MagicMock(spec=DecisionProvider)
        self.module.decision_provider = decisions
        self.module._resolve_user_action('I inspect the gate.', publish=False)
        decisions.decide.assert_not_called()
        request = self.provider.generate.call_args.args[0]
        self.assertEqual(request.system_instruction.count('Lore details about the realm.'), 1)
        self.assertNotIn('Lore details about the realm.', request.prompt)

    def test_game_entry_skips_dice_decision(self) -> None:
        decisions = MagicMock(spec=DecisionProvider)
        self.module.decision_provider = decisions
        self.theater.lore_documents.return_value = []
        self.provider.generate.return_value = TextResponseResult(
            text='{"narration":"The merchant hands over the key."}', provider='test', model='test',
        )
        self.module._resolve_user_action('A trade completed.', game_entry=True, publish=False)
        decisions.decide.assert_not_called()
        self.provider.generate.assert_called_once()

    def test_game_entry_rejects_factual_changes_without_retry(self) -> None:
        from components.story.story_response_module import GameEntryNarrationRejected
        self.provider.generate.return_value = TextResponseResult(
            text='{"narration":"A crown appears.","proposed_factual_changes":'
                 '[{"topic":"Inventory","info":"Crown","evidence":"Gift"}]}',
            provider='test', model='test',
        )
        with self.assertRaises(GameEntryNarrationRejected):
            self.module._resolve_user_action('A trade completed.', game_entry=True, publish=False)
        self.provider.generate.assert_called_once()
        self.assertFalse(self.module._game_entry)
        self.story_state.set_scene.assert_not_called()

    def test_timeout_does_not_wait_for_or_publish_late_response(self) -> None:
        release = Event()
        finished = Event()
        def generate(request: TextResponseRequest) -> TextResponseResult:
            try:
                release.wait(5)
                return TextResponseResult(text='{"narration":"Late result","dice_used":true}', provider='test', model='test')
            finally:
                finished.set()
        self.provider.generate.side_effect = generate
        self.module.user_action_timeout_seconds = 0.02
        start = time.monotonic()
        try:
            result = self.module._resolve_user_action('I wait.', publish=False)
            self.assertIn('timed out', result['error'])
            self.assertLess(time.monotonic() - start, 1)
        finally:
            release.set()
            self.assertTrue(finished.wait(2))
        self.story_state.set_scene.assert_not_called()
        self.story_state.record_die_roll.assert_not_called()
        self.assertEqual(self.module.get_die_rolls_this_turn(), [])

    def test_planning_cues_do_not_expose_peer_trigger_tools(self) -> None:
        self.assertIn("planning_request is advisory", self.module._build_responder_instruction())

    def test_pins_only_responder_guide_and_records_its_activity(self) -> None:
        self.theater.lore_documents.return_value = ["RESPONDER.md", "PLANNER.md", "read_legacy.txt"]
        contents = {
            "RESPONDER.md": "RESPONDER PRIORITY: resolve the scene.",
            "PLANNER.md": "PLANNER SECRET: hidden faction plan.",
            "read_legacy.txt": "LEGACY CONTENT: no longer pinned.",
        }
        self.theater.read_lore_document.side_effect = contents.__getitem__
        instruction = self.module._build_responder_instruction()
        self.assertIn(contents["RESPONDER.md"], instruction)
        self.assertNotIn(contents["PLANNER.md"], instruction)
        self.assertNotIn(contents["read_legacy.txt"], instruction)
        self.assertEqual(self.module.get_lore_docs_browsed_this_turn(), ["RESPONDER.md"])
        self.assertIn("private", self.module.read_lore("PLANNER.md"))

    def test_planning_request_includes_completed_turn_and_resets(self) -> None:
        def resolve(action: str, nudge: str = "") -> dict[str, str | list[str]]:
            self.assertIn("Story planning requested", self.module.trigger_story_planning("  Revise the quest  "))
            self.assertIn("already been requested", self.module.trigger_story_planning("Another reason"))
            return {"narration": "The key breaks.", "planning_signals": ["The key is unusable."]}

        with patch.object(self.module, "_generate_response", side_effect=resolve):
            result = self.module._resolve_user_action("I turn the key.")

        self.assertEqual(result["planning_request"], "Revise the quest")
        self.assertEqual(result["user_action"], "I turn the key.")
        self.assertEqual(result["narration"], "The key breaks.")
        self.assertEqual(result["planning_signals"], ["The key is unusable.", "[PLANNING REQUEST] Revise the quest"])
        with patch.object(self.module, "_generate_response", return_value={"narration": "Stillness."}):
            next_result = self.module._resolve_user_action("I wait.")
        self.assertNotIn("planning_request", next_result)
        self.assertEqual(next_result["planning_signals"], [])

    def test_result_records_notepad_revision_from_before_response_execution(self) -> None:
        self.module.notepad.update_sticky_note("Quest", "Find the gate")
        starting_revision = self.module.notepad.revision

        def resolve(action: str, nudge: str = "") -> dict[str, str]:
            self.module.notepad.update_sticky_note("Quest", "The gate is guarded")
            return {"narration": "A guard steps into view."}

        with patch.object(self.module, "_generate_response", side_effect=resolve):
            result = self.module._resolve_user_action("I approach the gate.")
        self.assertEqual(result["notepad_revision_used"], starting_revision)
        self.assertGreater(self.module.notepad.revision, starting_revision)
        self.assertNotIn("deep_plan_revision_used", result)

    def test_prompt_snapshot_revision_is_recorded_when_notes_change_before_prompt_build(
        self,
    ) -> None:
        self.module.notepad.update_sticky_note("Quest", "Find the gate")
        observed_prompt = ""

        def resolve(action: str, nudge: str = "") -> dict[str, str]:
            nonlocal observed_prompt
            self.module.notepad.update_sticky_note("Quest", "Open the gate")
            prompt_revision = self.module.notepad.revision
            observed_prompt = self.module._build_responder_turn_prompt(action, nudge=nudge)
            self.module.notepad.update_sticky_note("Quest", "Pass through the gate")
            self.assertGreater(self.module.notepad.revision, prompt_revision)
            return {"narration": "You step toward the gate."}

        with patch.object(self.module, "_generate_response", side_effect=resolve):
            result = self.module._resolve_user_action("I approach the gate.")
        self.assertIn("Open the gate", observed_prompt)
        self.assertEqual(result["notepad_revision_used"], 2)
        self.assertEqual(self.module.notepad.revision, 3)

    def test_structured_proposals_are_returned_without_changing_notes(self) -> None:
        proposal = {
            "topic": "Inventory",
            "info": "Broken key",
            "evidence": "The key snapped during the resolved attempt.",
        }
        before = self.module.notepad.export_state()
        with patch.object(self.module, "_generate_response", return_value={
            "narration": "The key breaks.",
            "proposed_factual_changes": [proposal],
            "planning_request": {"reason": "Find another route"},
        }):
            result = self.module._resolve_user_action("I turn the key.")
        self.assertEqual(result["proposed_factual_changes"], [proposal])
        self.assertEqual(result["planning_request"], "Find another route")
        self.assertEqual(self.module.notepad.export_state(), before)

    def test_invalid_proposal_is_rejected_before_scene_delivery(self) -> None:
        with (
            patch.object(self.module, "_generate_response", return_value={
                "narration": "The key breaks.",
                "proposed_factual_changes": [{"topic": "Inventory", "info": "Broken key"}],
            }),
            patch.object(self.module, "_publish_scene") as publish,
        ):
            with self.assertRaises(ValueError):
                self.module._resolve_user_action("I turn the key.")
        publish.assert_not_called()

    def test_failed_turn_discards_planning_request(self) -> None:
        def fail(action: str, nudge: str = "") -> dict[str, str]:
            self.module.trigger_story_planning("Revise the quest")
            raise RuntimeError("Responder failed")

        with patch.object(self.module, "_generate_response", side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, "Responder failed"):
                self.module._resolve_user_action("I turn the key.")
        self.assertIsNone(self.module._planning_request_reason)

    def test_planning_request_rejects_empty_or_oversized_reason(self) -> None:
        self.assertIn("reason is required", self.module.trigger_story_planning("  "))
        self.assertIn("500 characters or fewer", self.module.trigger_story_planning("x" * 501))
        self.assertIsNone(self.module._planning_request_reason)

    def test_dice_configuration_controls_tools_and_instructions(self) -> None:
        for settings, enabled in [({}, True), ({"enable_dice": True}, True), ({"enable_dice": False}, False)]:
            with self.subTest(settings=settings):
                self.theater.config.return_value = {"story_planning": settings}
                module = StoryResponseModule(
                    theater=self.theater,
                    canvas_manager=self.canvas,
                    lore_library=self.lore_library,
                    reference_manager=self.reference_manager,
                    text_response_provider=self.provider,
                    decision_provider=self.decisions,
                )
                instruction = module._build_responder_instruction()
                self.assertEqual("**Dice Results**" in instruction, enabled)
                self.assertEqual("Dice rolling is disabled" in instruction, not enabled)
                if not enabled:
                    for procedural in (False, True):
                        with patch("random.randint") as randint:
                            result = module.roll_dice(procedural=procedural)
                        self.assertEqual(result, {"error": "Dice rolling is disabled."})
                        randint.assert_not_called()
                    self.assertEqual(module.get_die_rolls_this_turn(), [])
                    self.canvas.tool_response.set_activity.assert_not_called()
                    self.story_state.record_die_roll.assert_not_called()

    def test_rolls_and_resets_dice(self) -> None:
        # 1. Player reactionary roll (default procedural=False) -> shown on canvas
        roll = self.module.roll_dice(
            sides=20,
            count=2,
            modifier=3,
            reason="perception check",
        )

        self.assertEqual(roll["sides"], 20)
        self.assertEqual(roll["count"], 2)
        self.assertEqual(roll["modifier"], 3)
        self.assertEqual(roll["total"], sum(roll["rolls"]) + 3)
        self.assertEqual(roll["notation"], "2d20+3")
        self.assertIn(roll["tier"], {"low", "middle", "high"})
        self.assertFalse(roll["procedural"])
        self.assertEqual(len(self.module.get_die_rolls_this_turn()), 1)
        self.canvas.tool_response.set_activity.assert_called_once_with(
            "dice",
            active=True,
            recent_seconds=5.0,
            result=roll,
        )
        self.story_state.record_die_roll.assert_called_once_with(roll)

        # 2. Procedural roll (procedural=True) -> hidden from canvas
        self.canvas.tool_response.set_activity.reset_mock()
        self.story_state.record_die_roll.reset_mock()

        proc_roll = self.module.roll_dice(
            sides=6,
            count=1,
            reason="weather check",
            procedural=True,
        )
        self.assertTrue(proc_roll["procedural"])
        self.canvas.tool_response.set_activity.assert_not_called()
        self.story_state.record_die_roll.assert_not_called()
        self.assertEqual(len(self.module.get_die_rolls_this_turn()), 2)

        self.module.reset_die_roll_counts()
        self.assertEqual(self.module.get_die_rolls_this_turn(), [])

    def test_d20_tiers_include_modifiers(self) -> None:
        cases = [
            (1, 0, "low"),
            (6, 0, "low"),
            (7, 0, "middle"),
            (13, 0, "middle"),
            (14, 0, "high"),
            (20, 0, "high"),
            (6, 1, "middle"),
            (7, -1, "low"),
            (13, 1, "high"),
            (14, -1, "middle"),
            (1, -100, "low"),
            (20, 100, "high"),
        ]
        for natural_roll, modifier, expected_tier in cases:
            with self.subTest(natural_roll=natural_roll, modifier=modifier):
                with patch("random.randint", return_value=natural_roll):
                    roll = self.module.roll_dice(modifier=modifier)
                self.assertEqual(roll["total"], natural_roll + modifier)
                self.assertEqual(roll["tier"], expected_tier)
                self.assertEqual(
                    self.story_state.record_die_roll.call_args.args[0]["tier"],
                    expected_tier,
                )
                self.assertEqual(
                    self.canvas.tool_response.set_activity.call_args.kwargs["result"]["tier"],
                    expected_tier,
                )

    def test_dice_tiers_scale_with_sides_and_count(self) -> None:
        cases = [
            (6, [2], 0, "low"),
            (6, [3], 0, "middle"),
            (6, [4], 0, "middle"),
            (6, [5], 0, "high"),
            (6, [2, 2], 0, "low"),
            (6, [2, 3], 0, "middle"),
            (6, [4, 4], 0, "middle"),
            (6, [4, 5], 0, "high"),
            (6, [2, 2], 1, "middle"),
            (6, [4, 5], -1, "middle"),
        ]
        for sides, rolls, modifier, expected_tier in cases:
            with self.subTest(sides=sides, rolls=rolls, modifier=modifier):
                with patch("random.randint", side_effect=rolls):
                    roll = self.module.roll_dice(
                        sides=sides,
                        count=len(rolls),
                        modifier=modifier,
                        procedural=True,
                    )
                self.assertEqual(roll["total"], sum(rolls) + modifier)
                self.assertEqual(roll["tier"], expected_tier)

    def test_owns_response_lore_budget_and_activity(self) -> None:
        for _ in range(3):
            self.assertIn("Lore details", self.module.read_lore("lore.txt"))

        self.assertIn(
            "Maximum read_lore call limit (3) reached",
            self.module.read_lore("lore.txt"),
        )
        self.assertEqual(len(self.module.get_lore_activity_this_turn()), 1)
        self.assertEqual(
            self.module.get_lore_docs_browsed_this_turn(),
            ["lore.txt"],
        )

        self.assertIn("Lore details", self.lore_library.read_lore("lore.txt"))

        self.module.reset_lore_call_counts()
        self.assertIn("Lore details", self.module.read_lore("lore.txt"))

    def test_reference_updates_return_while_generation_is_running(self) -> None:
        for kind in ("character", "scene"):
            with self.subTest(kind=kind):
                started = Event()
                release = Event()

                def generate(*args: str, **kwargs: str) -> Character | dict[str, str]:
                    started.set()
                    if not release.wait(5):
                        raise TimeoutError("Test did not release generation")
                    return Character(name="Mira", gender="female") if kind == "character" else {"name": "Mira"}

                target = "_create_or_update_character" if kind == "character" else "create_or_update_scene"
                owner = self.module if kind == "character" else self.reference_manager
                try:
                    with patch.object(owner, target, side_effect=generate):
                        message = (
                            self.module.create_or_update_character("Mira")
                            if kind == "character" else self.module.create_or_update_scene("Mira")
                        )
                        self.assertIn("started in background", message)
                        self.assertTrue(started.wait(1))
                        with self.assertRaises(TimeoutError):
                            self.reference_manager.wait_for_reference_updates(["Mira"], timeout=0)
                        release.set()
                        self.reference_manager.wait_for_reference_updates(["Mira"], timeout=5)
                finally:
                    release.set()

    def test_delegates_character_management_to_shared_manager(self) -> None:
        self.module.create_or_update_character(
            name="Kaelen",
            description="A stoic ranger",
            personality="Taciturn",
            motivation="Protect the woods",
            quirk="Whistles old tunes",
            voice_tags=["male"],
        )
        self.reference_manager.wait_for_reference_updates(["Kaelen"], timeout=5)

        characters = self.module.get_present_characters()
        self.assertEqual(len(characters), 1)
        self.assertEqual(characters[0]["name"], "Kaelen")
        self.assertEqual(characters[0]["voice_tags"], ["male"])
        lookup_result = self.module.lookup_character("ranger")
        self.assertEqual(len(lookup_result.characters), 1)
        self.assertEqual(lookup_result.characters[0].name, "Kaelen")
        self.assertIn("Kaelen", lookup_result)
        self.assertIn("Taciturn", lookup_result)

        self.module.clear_scene()
        self.assertEqual(self.module.get_present_characters(), [])

    def test_module_create_or_update_character_upsert_and_persists_to_session_state(self) -> None:
        result = self.module.create_or_update_character(
            name="Cedric",
            description="Knight of the Realm",
            personality="Brave and dutiful",
            motivation="Protect the innocent",
            quirk="Polishes visor constantly",
            gender="male",
        )
        self.assertIn("started in background", result)
        self.reference_manager.wait_for_reference_updates(["Cedric"], timeout=5)
        profile = self.module.lookup_character("Cedric").characters[0]
        self.assertIsNotNone(profile)
        self.assertEqual(profile["name"], "Cedric")
        self.assertEqual(profile["description"], "Knight of the Realm")
        self.assertEqual(profile["personality"], "Brave and dutiful")
        # Ensure session state persistence was triggered
        self.story_state.set_story_planning_state.assert_called()

        # Perform upsert on existing character
        self.module.create_or_update_character(
            name="Cedric",
            description="Knight Commander of the Realm",
        )
        self.reference_manager.wait_for_reference_updates(["Cedric"], timeout=5)
        updated = self.module.lookup_character("Cedric").characters[0]
        self.assertIsNotNone(updated)
        self.assertEqual(updated["description"], "Knight Commander of the Realm")
        self.assertEqual(updated["personality"], "Brave and dutiful")
        self.assertEqual(updated["motivation"], "Protect the innocent")
        self.assertEqual(updated["quirk"], "Polishes visor constantly")

        # Backwards compatibility check
        self.module.create_or_update_character(name="Cedric", description="Grand Marshal")
        self.reference_manager.wait_for_reference_updates(["Cedric"], timeout=5)
        via_alias = self.module.lookup_character("Cedric").characters[0]
        self.assertEqual(via_alias["description"], "Grand Marshal")

    def test_resolves_user_action_does_not_coordinate_planning(self) -> None:
        scene_delta = {
            "narration": "You slip through the shadowy arches of the ruined shrine.",
            "dialogue": [{"speaker": "Kaelen", "text": "Stay quiet."}],
            "manifested_characters": ["Kaelen"],
            "planning_signals": ["Shrine discovered", "Kaelen scouted"],
            "scene_reference": "Ruined Shrine",
        }

        player = PlayerCharacter(
            name="Arthur", image_description="A wizard", reference="private_alias",
            reference_path="private/Arthur/1.png", reference_source="existing",
        )
        with patch.object(self.module.reference_manager, "get_player_character", return_value=player), patch.object(
            self.module,
            "_generate_response",
            return_value=scene_delta,
        ):
            result = self.module._resolve_user_action("I sneak into the shrine.")

        self.assertEqual(result["narration"], scene_delta["narration"])
        self.assertEqual(result["scene_reference"], "Ruined Shrine")
        self.assertEqual(result["player_character"], {"name": "Arthur", "image_description": "A wizard"})
        self.assertEqual(player.reference_path, "private/Arthur/1.png")
        self.story_state.set_scene.assert_called_once_with(
            scene_delta["narration"],
            [{"speaker": "Kaelen", "text": "Stay quiet.", "kind": "speech"}],
        )

    def test_response_character_validates_and_normalizes_gender(self) -> None:
        char1 = ResponseCharacter(name="Cedric", gender="male")
        self.assertEqual(char1.gender, "male")
        self.assertEqual(char1.voice_tags, ["male"])

        char2 = ResponseCharacter(name="Mara", gender="female")
        self.assertEqual(char2.gender, "female")
        self.assertEqual(char2.voice_tags, ["female"])

        char3 = ResponseCharacter(name="Rowan", gender="nonbinary")
        self.assertEqual(char3.gender, "nonbinary")
        self.assertEqual(char3.voice_tags, ["nonbinary"])

        char4 = ResponseCharacter(name="Ash", gender="nb")
        self.assertEqual(char4.gender, "nonbinary")
        self.assertEqual(char4.voice_tags, ["nonbinary"])

    def test_response_character_requires_gender(self) -> None:
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            ResponseCharacter(name="Nameless")  # type: ignore[call-arg]

        with self.assertRaises(ValidationError):
            ResponseCharacter(name="Invalid", gender="alien")

    def test_resolves_user_action_saves_to_session_state_before_publishing_scene(self) -> None:
        call_order: list[str] = []
        self.module.save_to_session_state = lambda: call_order.append("save_to_session_state")
        self.story_state.set_scene.side_effect = lambda *args, **kwargs: call_order.append("set_scene")

        scene_delta = {
            "narration": "A newcomer approaches.",
            "dialogue": [{"speaker": "Rowan", "text": "Greetings."}],
            "manifested_characters": ["Rowan"],
            "planning_signals": [],
            "scene_reference": "Town Square",
        }

        with patch.object(self.module, "_generate_response", return_value=scene_delta):
            self.module._resolve_user_action("I look around.")

        self.assertEqual(call_order, ["save_to_session_state", "set_scene"])


class TestBuildStoryContextPrompt(unittest.TestCase):
    def _elements(self, *topics_infos: tuple[str, str]) -> list[dict[str, str]]:
        return [{"topic": t, "info": i, "name": t, "content": i} for t, i in topics_infos]

    def test_annotates_changed_topics_and_omits_unchanged(self) -> None:
        elements = self._elements(("HUD", "HP: 9"), ("Quest", "Find the key"), ("Location", "Observatory"))
        prompt = build_story_context_prompt(
            elements=elements,
            characters=[],
            changed_topics=frozenset({"HUD", "Location"}),
        )
        self.assertIn("HUD [\u2191 UPDATED]", prompt)
        self.assertIn("Location [\u2191 UPDATED]", prompt)
        self.assertNotIn("Quest [\u2191 UPDATED]", prompt)
        self.assertIn("Pay close attention", prompt)

    def test_no_annotation_when_no_changed_topics(self) -> None:
        elements = self._elements(("HUD", "HP: 10"))
        prompt = build_story_context_prompt(
            elements=elements,
            characters=[],
            changed_topics=frozenset(),
        )
        self.assertNotIn("UPDATED", prompt)
        self.assertNotIn("Pay close attention", prompt)


class TestBuildSceneReactionPrompt(unittest.TestCase):
    def test_instructs_planner_communication_and_sticky_note_recall(self) -> None:
        prompt = build_responder_instructions(
            context="Your sticky notes:\n- HUD: HP: 100/100",
            style="heroic fantasy",
            lore_context="",
        )
        self.assertIn("planning_signals", prompt)
        self.assertIn("STICKY UPDATE", prompt)
        self.assertIn("A recorder reconciles established facts", prompt)
        self.assertIn("proposed_factual_changes", prompt)
        self.assertIn("scene_reference", prompt)

    def test_scene_reaction_model_validates_sticky_update_signals(self) -> None:
        reaction = SceneReaction(
            narration="You strike the golem, taking 15 damage in return.",
            planning_signals=[
                "[STICKY UPDATE: Combat Stats & Synergy] HP reduced to 85/100",
                "Golem outer shell shattered",
            ],
        )
        self.assertEqual(len(reaction.planning_signals), 2)
        self.assertIn("[STICKY UPDATE: Combat Stats & Synergy]", reaction.planning_signals[0])

    def test_scene_reaction_schema_fields(self) -> None:
        fields = set(SceneReaction.model_fields.keys())
        self.assertEqual(
            fields,
            {
                "narration", "dialogue", "manifested_characters", "planning_signals",
                "scene_reference", "proposed_factual_changes", "planning_request",
                "dice_used", "dice_procedural", "dice_reason",
            },
        )
        self.assertNotIn("character_updates", fields)
        self.assertNotIn("player_character_update", fields)
        self.assertNotIn("reference_images", fields)

    def test_scene_reaction_model_validates_scene_reference(self) -> None:
        reaction = SceneReaction(
            narration="You enter the quiet valley under the twin moons.",
            scene_reference="Quiet Valley",
        )
        self.assertEqual(reaction.scene_reference, "Quiet Valley")


class TestStoryResponseModulePlayerCharacter(unittest.TestCase):
    def setUp(self) -> None:
        self.theater_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.theater_directory.cleanup)
        self.theater = MagicMock(
            spec=Theater,
            wraps=TheaterManager(Path(self.theater_directory.name)).theater("test_theater"),
        )
        self.theater.theater_id = "test_player_theater"
        self.theater.config.return_value = {"story_planning": {"adventure_mode": True, "enforce_structured": False}}
        self.canvas = MagicMock(spec=CanvasStateManager)
        self.lore_library = MagicMock(spec=LoreLibrary)
        self.notepad = Notepad(self.theater, canvas_manager=self.canvas)
        self.canvas.notepad = self.notepad
        self.provider = MagicMock(spec=TextResponseProvider)
        self.reference_manager = MagicMock(spec=ReferenceManager)
        self.decision_provider = MagicMock(spec=DecisionProvider)
        self.module = StoryResponseModule(
            theater=self.theater,
            canvas_manager=self.canvas,
            lore_library=self.lore_library,
            reference_manager=self.reference_manager,
            text_response_provider=self.provider,
            decision_provider=self.decision_provider,
        )

    def test_delegates_player_character_management(self) -> None:
        self.reference_manager.get_player_character.return_value = PlayerCharacter(
            name="Valen", image_description="Armored knight", reference="valen_img"
        )
        self.reference_manager.get_player_reference.return_value = "valen_img"
        self.reference_manager.update_player_character.return_value = PlayerCharacter(
            name="Valen", image_description="Armored knight", reference="valen_img"
        )

        player = self.module.get_player_character()
        self.assertEqual(player.name, "Valen")
        self.assertEqual(self.module.get_player_reference(), "valen_img")

        res = self.module.update_player_character(name="Valen", image_description="Armored knight")
        self.assertIn("Valen", res)
        self.reference_manager.update_player_character.assert_called_once()

    def test_build_story_context_prompt_includes_player_character(self) -> None:
        player = PlayerCharacter(
            name="Kael",
            image_description="Young archer in green cloak",
            reference="kael_ref",
        )
        prompt = build_story_context_prompt(
            elements=[{"topic": "Quest", "info": "Find the orb"}],
            characters=[],
            player_character=player,
        )
        self.assertIn("Canonical player character:", prompt)
        self.assertIn("Kael", prompt)
        self.assertIn("Young archer in green cloak", prompt)
        self.assertNotIn("kael_ref", prompt)


    def test_delegates_create_or_update_character(self) -> None:
        self.reference_manager.create_or_update_character.return_value = Character(
            name="Cedric", gender="male", image_reference="private_alias",
            image_reference_path="private/Cedric/1.png",
        )
        res = self.module._create_or_update_character(name="Cedric", gender="male")
        self.assertEqual(res["name"], "Cedric")
        self.assertIsNone(res.image_reference)
        self.assertIsNone(res.image_reference_path)
        self.reference_manager.create_or_update_character.assert_called_once_with(
            name="Cedric",
            description="",
            personality="",
            motivation="",
            quirk="",
            voice_tags=None,
            gender="male",
        )

    def test_delegates_create_or_update_scene(self) -> None:
        self.reference_manager.create_or_update_scene.return_value = {
            "name": "Ruined Keep",
            "path": "scenes/ruined_keep/1.png",
            "alias": "ruined_keep",
        }
        res = self.module.create_or_update_scene("Ruined Keep", "Dark ruins")
        self.assertIn("Ruined Keep", res)
        self.assertNotIn("scenes/ruined_keep/1.png", res)
        self.reference_manager.submit_reference_update.assert_called_once()
        self.reference_manager.create_or_update_scene.assert_not_called()
        self.reference_manager.submit_reference_update.call_args.args[2]()
        self.reference_manager.create_or_update_scene.assert_called_once_with(
            name="Ruined Keep",
            description="Dark ruins",
        )


class TestResponderInstructionAndTurnPrompt(unittest.TestCase):
    def setUp(self) -> None:
        self.theater_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.theater_directory.cleanup)
        self.theater = MagicMock(
            spec=Theater,
            wraps=TheaterManager(Path(self.theater_directory.name)).theater("test_theater"),
        )
        self.theater.theater_id = "test_prompt_theater"
        self.theater.lore_documents.return_value = ["lore.md"]
        self.theater.config.return_value = {
            "story_planning": {
                "adventure_mode": True,
                "style": "Mythic space opera",
                "enforce_structured": False,
            }
        }
        self.canvas = MagicMock(spec=CanvasStateManager)
        self.lore_library = MagicMock(spec=LoreLibrary)
        self.lore_library.get_lore_context.return_value = "Lore docs: space_history.md"
        self.notepad = Notepad(self.theater, canvas_manager=self.canvas)
        self.canvas.notepad = self.notepad
        self.provider = MagicMock(spec=TextResponseProvider)
        self.reference_manager = MagicMock(spec=ReferenceManager)
        self.decision_provider = MagicMock(spec=DecisionProvider)
        self.module = StoryResponseModule(
            theater=self.theater,
            canvas_manager=self.canvas,
            lore_library=self.lore_library,
            reference_manager=self.reference_manager,
            text_response_provider=self.provider,
            decision_provider=self.decision_provider,
        )

    def test_instruction_maximizes_prefix_and_excludes_state_variables(self) -> None:
        instruction = self.module._build_responder_instruction()
        self.assertIn("# Role & Mission", instruction)
        self.assertIn("# Core Improv & Player Agency Principles", instruction)
        self.assertIn("# Player Death, Lethal Consequences, Death Hints & Restarts", instruction)
        self.assertIn("# Supplied Context", instruction)
        self.assertIn("# Scene Reaction Output Requirements", instruction)
        self.assertIn("# Character Generation", instruction)
        self.assertIn("# Scene Reference", instruction)

        # Style and lore variable values are placed at the end to maximize prefix caching
        self.assertIn("# Story-Planning Style (User Specified)\nMythic space opera", instruction)
        self.assertIn("Available theater lore (top-level documents and directories):\nLore docs: space_history.md", instruction)

        role_idx = instruction.index("# Role & Mission")
        output_idx = instruction.index("# Scene Reaction Output Requirements")
        style_idx = instruction.index("# Story-Planning Style")
        lore_idx = instruction.index("Available theater lore")
        self.assertLess(role_idx, output_idx)
        self.assertLess(output_idx, style_idx)
        self.assertLess(style_idx, lore_idx)

        # Ensure dynamic state variables are NOT in instructions to maximize prefix caching
        self.assertNotIn("Your sticky notes", instruction)
        self.assertNotIn("Canonical player character", instruction)
        self.assertNotIn("Active characters, personalities", instruction)
        self.assertNotIn("[↑ UPDATED]", instruction)

    def test_turn_prompt_includes_current_story_state_and_puts_action_at_end(self) -> None:
        self.notepad.update_sticky_note("HUD", "HP: 45/100")
        self.notepad.update_sticky_note("Mission", "Reach the bridge")

        player = PlayerCharacter(name="Zara", image_description="Cybernetic pilot", reference="zara_ref")
        self.reference_manager.get_player_character.return_value = player
        char = Character(name="Kaelen", personality="Stoic", motivation="Survive", quirk="Tinkers with gadgets", voice_tags=["male"])
        self.reference_manager.get_present_characters.return_value = [char]
        self.reference_manager.count.return_value = 1

        prompt = self.module._build_responder_turn_prompt("I slice the security console.", nudge="Alert nearby guards")

        self.assertIn("# Current Story State", prompt)
        self.assertIn("HUD [↑ UPDATED]: HP: 45/100", prompt)
        self.assertIn("Mission [↑ UPDATED]: Reach the bridge", prompt)
        self.assertIn("Zara", prompt)
        self.assertIn("Kaelen", prompt)

        self.assertIn("# Player Action", prompt)
        self.assertIn("I slice the security console.", prompt)
        self.assertIn("[Live Agent Nudge to Accommodate]: Alert nearby guards", prompt)

        # Story state is prefix; variable player action and nudge are at the end
        state_idx = prompt.index("# Current Story State")
        action_idx = prompt.index("# Player Action")
        nudge_idx = prompt.index("[Live Agent Nudge to Accommodate]: Alert nearby guards")
        self.assertLess(state_idx, action_idx)
        self.assertLess(action_idx, nudge_idx)

        # mark_stickies_read should have been called during turn prompt building
        next_prompt = self.module._build_responder_turn_prompt("I wait.")
        self.assertNotIn("[↑ UPDATED]", next_prompt)

    def test_build_responder_turn_prompt_function(self) -> None:
        prompt = build_responder_turn_prompt(
            story_state="Your sticky notes:\n- HUD: HP: 100",
            user_action="I look north.",
            nudge="Fog rolls in.",
        )
        self.assertTrue(prompt.startswith("# Current Story State\nYour sticky notes:\n- HUD: HP: 100"))
        self.assertIn("# Player Action\nI look north.\n\n[Live Agent Nudge to Accommodate]: Fog rolls in.", prompt)

    def test_turn_prompt_excludes_game_state(self) -> None:
        from unittest.mock import Mock
        from game_engine.game_state import GameState

        self.module.game_state = GameState(Path("adventures/example_adventure"), Mock(), Mock())
        self.reference_manager.count.return_value = 0
        self.reference_manager.get_player_character.return_value = None
        prompt = self.module._build_responder_turn_prompt("I inspect the game board.")
        self.assertNotIn("# Current Game State", prompt)
        self.assertNotIn('"board"', prompt)
        self.assertNotIn('"instructions"', prompt)
        self.assertNotIn('"actions"', prompt)




class TestStoryResponseLoreDecisionProvider(unittest.TestCase):
    def setUp(self) -> None:
        self.theater_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.theater_dir.cleanup)
        self.theater = MagicMock(
            spec=Theater,
            wraps=TheaterManager(Path(self.theater_dir.name)).theater("lore_decision_test"),
        )
        self.theater.theater_id = "lore_decision_test"
        self.config = {
            "adventure_mode": True,
            "notepad": {"initial_stickies": {"location": "The Discarded Stacks", "quest": "Find the grimoire"}},
        }
        self.theater.config.return_value = self.config
        self.lore_documents = ["locations/the_discarded_stacks.txt", "characters/the_caretaker.txt"]
        self.theater.lore_documents.return_value = list(self.lore_documents)

        def mock_read_lore(doc: str) -> str:
            if doc == "locations/the_discarded_stacks.txt":
                return "The discarded stacks contain forgotten grimoires."
            if doc == "characters/the_caretaker.txt":
                return "The Caretaker oversees the ancient archives."
            return f"Content for {doc}"

        self.theater.read_lore_document.side_effect = mock_read_lore

        self.canvas_manager = MagicMock(spec=CanvasStateManager)
        self.canvas_manager.story = MagicMock(spec=StoryState)
        self.canvas_manager.tool_response = MagicMock()
        self.notepad = Notepad(self.theater, canvas_manager=self.canvas_manager)
        self.canvas_manager.notepad = self.notepad
        self.lore_library = LoreLibrary(theater=self.theater)
        self.reference_manager = MagicMock(spec=ReferenceManager)
        self.reference_manager.story_state = None
        self.reference_manager.count.return_value = 0
        self.reference_manager.get_present_characters.return_value = []
        self.reference_manager.get_player_character.return_value = None
        self.provider = MagicMock(spec=TextResponseProvider)
        self.decision_provider = MagicMock(spec=DecisionProvider)

        self.module = StoryResponseModule(
            theater=self.theater,
            canvas_manager=self.canvas_manager,
            lore_library=self.lore_library,
            reference_manager=self.reference_manager,
            text_response_provider=self.provider,
            decision_provider=self.decision_provider,
        )

    def test_collect_relevant_lore_reviews_sticky_state_and_action(self) -> None:
        mock_decision_provider = MagicMock(spec=DecisionProvider)
        mock_decision_provider.decide.return_value = DecisionResult(
            provider="test_provider",
            model="test_model",
            answers={
                "primary_lore_document": ChoiceAnswer(
                    name="primary_lore_document", choice="locations/the_discarded_stacks.txt"
                ),
                "secondary_lore_document": ChoiceAnswer(
                    name="secondary_lore_document", choice="none"
                ),
            }
        )
        self.module.decision_provider = mock_decision_provider

        lore = self.module.collect_relevant_lore(
            user_action="I search the shelves for the ancient book.",
            nudge="A shadow looms behind you.",
        )

        mock_decision_provider.decide.assert_called_once()
        req: DecisionRequest = mock_decision_provider.decide.call_args[0][0]

        # Verify input_text contains player action, nudge, and active sticky notes
        self.assertIn("Player Action:\nI search the shelves for the ancient book.", req.input_text)
        self.assertIn("Live Agent Nudge:\nA shadow looms behind you.", req.input_text)
        self.assertIn("Active Sticky Notes:", req.input_text)
        self.assertIn("- location: The Discarded Stacks", req.input_text)
        self.assertIn("- quest: Find the grimoire", req.input_text)

        # Verify questions
        q_names = [q.name for q in req.questions]
        self.assertEqual(q_names, ["primary_lore_document", "secondary_lore_document"])

        # Verify populated lore
        self.assertIn("## Lore Document: locations/the_discarded_stacks.txt", lore)
        self.assertIn("The discarded stacks contain forgotten grimoires.", lore)
        self.assertNotIn("characters/the_caretaker.txt", lore)
        self.assertIn("locations/the_discarded_stacks.txt", self.module.get_lore_docs_browsed_this_turn())

    def test_collect_relevant_lore_with_secondary_lore(self) -> None:
        mock_decision_provider = MagicMock(spec=DecisionProvider)
        mock_decision_provider.decide.return_value = DecisionResult(
            provider="test_provider",
            model="test_model",
            answers={
                "primary_lore_document": ChoiceAnswer(
                    name="primary_lore_document", choice="characters/the_caretaker.txt"
                ),
                "secondary_lore_document": ChoiceAnswer(
                    name="secondary_lore_document", choice="locations/the_discarded_stacks.txt"
                ),
            }
        )
        self.module.decision_provider = mock_decision_provider

        lore = self.module.collect_relevant_lore(user_action="I ask the Caretaker about the stacks.")
        self.assertIn("## Lore Document: characters/the_caretaker.txt", lore)
        self.assertIn("The Caretaker oversees the ancient archives.", lore)
        self.assertIn("## Lore Document: locations/the_discarded_stacks.txt", lore)
        self.assertIn("The discarded stacks contain forgotten grimoires.", lore)

    def test_collect_relevant_lore_when_decision_provider_returns_none(self) -> None:
        mock_decision_provider = MagicMock(spec=DecisionProvider)
        mock_decision_provider.decide.return_value = DecisionResult(
            provider="test_provider",
            model="test_model",
            answers={
                "primary_lore_document": ChoiceAnswer(
                    name="primary_lore_document", choice="none"
                ),
                "secondary_lore_document": ChoiceAnswer(
                    name="secondary_lore_document", choice="none"
                ),
            }
        )
        self.module.decision_provider = mock_decision_provider

        lore = self.module.collect_relevant_lore(user_action="I stretch my arms and yawn.")
        self.assertEqual(lore, "")
        self.assertEqual(self.module.get_lore_docs_browsed_this_turn(), [])

    def test_collect_relevant_lore_without_decision_provider(self) -> None:
        self.module.decision_provider = None
        lore = self.module.collect_relevant_lore(user_action="I look around.")
        self.assertEqual(lore, "")

    def test_collect_relevant_lore_with_empty_candidate_docs(self) -> None:
        self.theater.lore_documents.return_value = []
        mock_decision_provider = MagicMock(spec=DecisionProvider)
        self.module.decision_provider = mock_decision_provider

        lore = self.module.collect_relevant_lore(user_action="I look around.")
        self.assertEqual(lore, "")
        mock_decision_provider.decide.assert_not_called()

    def test_turn_prompt_includes_relevant_lore_section(self) -> None:
        prompt = build_responder_turn_prompt(
            story_state="Notes here",
            user_action="I investigate the arch.",
            relevant_lore="Arch lore excerpt",
        )
        self.assertIn("# Relevant Lore Context\nArch lore excerpt", prompt)

    def test_decision_provider_is_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "decision_provider is required"):
            StoryResponseModule(
                theater=self.theater,
                canvas_manager=self.canvas_manager,
                lore_library=self.lore_library,
                reference_manager=self.reference_manager,
                text_response_provider=self.provider,
                decision_provider=None,  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
