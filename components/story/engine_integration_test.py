"""Full-stack integration tests for ``StoryEngine`` across API failures and timings.

Every story subsystem is instantiated for real — ``Theater``, ``CanvasStateManager``,
``StoryState``, ``Notepad``, ``GameState``, ``LoreLibrary``, ``ReferenceManager``,
``StoryResponseModule``, ``StoryGameModule``, ``StoryRecorderModule``,
``StoryPlanningModule``, ``StorySession`` (ADK ``Runner`` + SQLite session service +
``VertexGemini``), ``BackgroundPlanning``, ``StoryTurnJournal``, ``StoryRecordingJournal``,
``StoryInputGatheringCycle``, ``StoryOutputDisplayCycle``, and ``TextBeautifier``.

Only the actual AI API backends are mocked at their provider/SDK boundaries:
- ``TextResponseProvider`` (responder, game controller, and text beautifier)
- ``DecisionProvider`` (lore pre-selection and story-to-game gating)
- ``google.genai.Client`` used by ``VertexGemini`` (ADK recorder and deep planner)
- ``SpeechProvider`` (canvas scene TTS synthesis)
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import json
from pathlib import Path
from threading import Event, Lock
import time
from typing import Literal
from unittest.mock import patch

from google.genai import errors as genai_errors
from google.genai import types
from pydantic import JsonValue
import pytest
import yaml

from components.canvas.canvas_state_manager import CanvasStateManager
from components.lore_library import LoreLibrary
from components.reference_manager import ReferenceManager
from components.story.engine import StoryEngine
from components.story.story_handoffs import (
    CanonicalReferenceUpdate,
    GameControlDecision,
    NarrationRequest,
)
from components.story.story_response_module import SceneReaction
from components.theater_manager import Theater, TheaterManager
from game_engine.game_runtime import GameAction, GameDefinition
from providers.decision_provider import (
    ChoiceAnswer,
    DecisionAnswer,
    DecisionProvider,
    DecisionProviderError,
    DecisionRequest,
    DecisionResult,
    PredicateAnswer,
)
from providers.speech_provider import (
    SpeechProvider,
    SpeechProviderError,
    SpeechSynthesisRequest,
    SpeechSynthesisResult,
)
from providers.text_response_provider import (
    TextResponseProvider,
    TextResponseProviderError,
    TextResponseRequest,
    TextResponseResult,
)
from services.text_beautifier import TextBeautifier
from testing.game_fixtures import exchange_definition


RESPONDER_MODEL = "fake-responder-model"
GAME_MODEL = "fake-game-model"
RECORDER_MODEL = "fake-recorder-model"
PLANNER_MODEL = "fake-planner-model"
BEAUTIFIER_MODEL = "fake-beautifier-model"


# ---------------------------------------------------------------------------
# Helpers for constructing real google.genai responses
# ---------------------------------------------------------------------------


def make_genai_text_response(
    text: str,
    *,
    prompt_tokens: int = 80,
    candidates_tokens: int = 30,
    thoughts_tokens: int = 10,
) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text=text)]),
                finish_reason=types.FinishReason.STOP,
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=candidates_tokens,
            thoughts_token_count=thoughts_tokens,
            total_token_count=prompt_tokens + candidates_tokens + thoughts_tokens,
        ),
    )


def make_genai_tool_call_response(
    name: str,
    args: dict[str, JsonValue],
    call_id: str,
    *,
    prompt_tokens: int = 80,
    candidates_tokens: int = 30,
    thoughts_tokens: int = 10,
) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                name=name,
                                args=args,
                                id=call_id,
                            )
                        )
                    ],
                ),
                finish_reason=types.FinishReason.STOP,
            )
        ],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=candidates_tokens,
            thoughts_token_count=thoughts_tokens,
            total_token_count=prompt_tokens + candidates_tokens + thoughts_tokens,
        ),
    )


def make_genai_malformed_response() -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                finish_reason=types.FinishReason.MALFORMED_FUNCTION_CALL,
                finish_message="Simulated malformed function call.",
            )
        ]
    )


def make_rate_limit_error(message: str = "Quota exceeded") -> genai_errors.ClientError:
    return genai_errors.ClientError(
        429,
        {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": message}},
    )


def make_server_error(message: str = "Backend unavailable") -> genai_errors.ServerError:
    return genai_errors.ServerError(
        503,
        {"error": {"code": 503, "status": "UNAVAILABLE", "message": message}},
    )


def flatten_genai_contents(contents: Sequence[types.Content]) -> str:
    return json.dumps(
        [content.model_dump(mode="json") for content in contents],
        ensure_ascii=False,
    )


def recorder_set_model_response(
    *,
    reason: str = "Recorded factual updates.",
    factual_updates: dict[str, str] | None = None,
    reference_updates: Sequence[CanonicalReferenceUpdate] = (),
    call_id: str = "recorder-final",
) -> types.GenerateContentResponse:
    decision = GameControlDecision(
        narration_request=None,
        reason=reason,
        factual_updates=factual_updates or {},
        reference_updates=tuple(reference_updates),
    )
    return make_genai_tool_call_response(
        "set_model_response",
        decision.model_dump(mode="json"),
        call_id,
    )


# ---------------------------------------------------------------------------
# Fake AI API backends (strictly typed, zero mocks inside StoryEngine)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CapturedTextCall:
    role: Literal["responder", "game", "beautifier"]
    request: TextResponseRequest
    timestamp: float


TextHandler = Callable[[TextResponseRequest], TextResponseResult]


class FakeTextResponseProvider(TextResponseProvider):
    """Programmable TextResponseProvider for responder, game, and beautifier APIs."""

    def __init__(self, default_model: str = RESPONDER_MODEL) -> None:
        self.id = "fake-text"
        self.display_name = "Fake Text Response Provider"
        self.model = default_model
        self._lock = Lock()
        self.calls: list[CapturedTextCall] = []
        self.responder_handler: TextHandler = self._default_responder
        self.game_handler: TextHandler = self._default_game
        self.beautifier_handler: TextHandler = self._default_beautifier

    def _classify(self, request: TextResponseRequest) -> Literal["responder", "game", "beautifier"]:
        if request.model == GAME_MODEL:
            return "game"
        if request.model == RESPONDER_MODEL or request.response_schema is SceneReaction:
            return "responder"
        if request.response_schema is GameControlDecision:
            return "game"
        if request.response_json_schema is not None:
            props = request.response_json_schema.get("properties")
            if type(props) is dict and "game_actions" in props:
                return "game"
        return "beautifier"

    def _default_responder(self, request: TextResponseRequest) -> TextResponseResult:
        payload = {
            "narration": "The brass lock yields with a crisp click.",
            "dialogue": [],
            "proposed_factual_changes": [],
        }
        return TextResponseResult(
            text=json.dumps(payload),
            provider=self.id,
            model=request.model or RESPONDER_MODEL,
        )

    def _default_game(self, request: TextResponseRequest) -> TextResponseResult:
        decision = GameControlDecision(
            game_actions=(),
            narration_request=None,
            reason="Routine mechanical state.",
        )
        return TextResponseResult(
            text=decision.model_dump_json(),
            provider=self.id,
            model=request.model or GAME_MODEL,
        )

    def _default_beautifier(self, request: TextResponseRequest) -> TextResponseResult:
        return TextResponseResult(
            text=json.dumps({
                "narration_spans": [
                    {"text": "Styled scene.", "effect": "glow", "font": "cinematic", "color": None}
                ],
                "dialogue": [],
            }),
            provider=self.id,
            model=BEAUTIFIER_MODEL,
        )

    def generate(self, request: TextResponseRequest) -> TextResponseResult:
        role = self._classify(request)
        with self._lock:
            self.calls.append(CapturedTextCall(role=role, request=request, timestamp=time.monotonic()))
            if role == "responder":
                handler = self.responder_handler
            elif role == "game":
                handler = self.game_handler
            else:
                handler = self.beautifier_handler
        return handler(request)

    def calls_for(self, role: Literal["responder", "game", "beautifier"]) -> list[CapturedTextCall]:
        with self._lock:
            return [call for call in self.calls if call.role == role]


DecisionHandler = Callable[[DecisionRequest], DecisionResult]


class FakeDecisionProvider(DecisionProvider):
    """Programmable DecisionProvider for lore pre-selection and game gating."""

    def __init__(self) -> None:
        self.id = "fake-decision"
        self.display_name = "Fake Decision Provider"
        self.model = "fake-decision-model"
        self._lock = Lock()
        self.requests: list[DecisionRequest] = []
        self.handler: DecisionHandler = self._default_decide

    def _default_decide(self, request: DecisionRequest) -> DecisionResult:
        answers: dict[str, DecisionAnswer] = {}
        for question in request.questions:
            if question.question_type == "predicate":
                answers[question.name] = PredicateAnswer(name=question.name, probability=0.10)
            else:
                answers[question.name] = ChoiceAnswer(name=question.name, choice="none")
        return DecisionResult(provider=self.id, model=self.model, answers=answers)

    def decide(self, request: DecisionRequest) -> DecisionResult:
        with self._lock:
            self.requests.append(request)
            handler = self.handler
        return handler(request)


@dataclass(frozen=True)
class CapturedGenAICall:
    role: Literal["recorder", "planner"]
    model: str
    contents: list[types.Content]
    config: types.GenerateContentConfig | None
    timestamp: float


GenAIHandler = Callable[
    [str, list[types.Content], types.GenerateContentConfig | None],
    types.GenerateContentResponse,
]
AsyncGenAIHandler = Callable[
    [str, list[types.Content], types.GenerateContentConfig | None],
    asyncio.Future[types.GenerateContentResponse] | types.GenerateContentResponse,
]


class _FakeAsyncModels:
    def __init__(self, client: FakeGenAIClient) -> None:
        self._client = client

    async def generate_content(
        self,
        *,
        model: str,
        contents: list[types.Content],
        config: types.GenerateContentConfig | None = None,
    ) -> types.GenerateContentResponse:
        return await self._client._dispatch(model, contents, config)


class _FakeAioNamespace:
    def __init__(self, client: FakeGenAIClient) -> None:
        self.models = _FakeAsyncModels(client)


class FakeGenAIClient:
    """Fake google.genai.Client injected into ``VertexGemini.api_client``."""

    def __init__(self) -> None:
        self.vertexai: bool = True
        self.aio = _FakeAioNamespace(self)
        self._lock = Lock()
        self.calls: list[CapturedGenAICall] = []
        self.recorder_handler: Callable[
            [list[types.Content], types.GenerateContentConfig | None],
            types.GenerateContentResponse,
        ] = self._default_recorder
        self.planner_handler: Callable[
            [list[types.Content], types.GenerateContentConfig | None],
            types.GenerateContentResponse,
        ] = self._default_planner
        self.recorder_async_hook: Callable[[list[types.Content]], asyncio.coroutines] | None = None
        self.planner_delay_seconds: float = 0.0
        self.recorder_delay_seconds: float = 0.0

    def _default_recorder(
        self,
        contents: list[types.Content],
        config: types.GenerateContentConfig | None,
    ) -> types.GenerateContentResponse:
        return recorder_set_model_response(
            factual_updates={"Inventory": "Brass key"},
            reason="Default recorded update.",
            call_id=f"rec-{len(self.calls)}",
        )

    def _default_planner(
        self,
        contents: list[types.Content],
        config: types.GenerateContentConfig | None,
    ) -> types.GenerateContentResponse:
        return make_genai_text_response("Planning complete.")

    async def _dispatch(
        self,
        model: str,
        contents: list[types.Content],
        config: types.GenerateContentConfig | None,
    ) -> types.GenerateContentResponse:
        role: Literal["recorder", "planner"] = "recorder" if model == RECORDER_MODEL else "planner"
        copied_contents = [content.model_copy(deep=True) for content in contents]
        copied_config = config.model_copy(deep=True) if config is not None else None
        with self._lock:
            self.calls.append(
                CapturedGenAICall(
                    role=role,
                    model=model,
                    contents=copied_contents,
                    config=copied_config,
                    timestamp=time.monotonic(),
                )
            )
            delay = self.recorder_delay_seconds if role == "recorder" else self.planner_delay_seconds
            handler = self.recorder_handler if role == "recorder" else self.planner_handler
        if delay > 0.0:
            await asyncio.sleep(delay)
        # Run handler in a worker thread so blocking Event.wait() barriers in tests
        # never freeze the shared story-adk asyncio event loop for concurrent roles!
        return await asyncio.to_thread(handler, copied_contents, copied_config)

    def calls_for(self, role: Literal["recorder", "planner"]) -> list[CapturedGenAICall]:
        with self._lock:
            return [call for call in self.calls if call.role == role]


SpeechHandler = Callable[[SpeechSynthesisRequest], SpeechSynthesisResult]


class FakeSpeechProvider(SpeechProvider):
    """Programmable SpeechProvider for canvas TTS synthesis."""

    def __init__(self) -> None:
        self.id = "fake-speech"
        self.display_name = "Fake Speech Provider"
        self.model = "fake-speech-model"
        self._lock = Lock()
        self.requests: list[SpeechSynthesisRequest] = []
        self.handler: SpeechHandler = self._default_synthesize

    def _default_synthesize(self, request: SpeechSynthesisRequest) -> SpeechSynthesisResult:
        return SpeechSynthesisResult(
            audio_bytes=f"AUDIO:{request.text}".encode("utf-8"),
            mime_type="audio/mpeg",
            provider=self.id,
            model=self.model,
        )

    def select_voice(
        self,
        voice_tags: Sequence[str] | str | Mapping[str, JsonValue] | None = None,
        *,
        exclude: Sequence[str] | set[str] | None = None,
    ) -> str:
        excluded = set(exclude or ())
        for candidate in ("voice-aria", "voice-bram", "voice-cora", "voice-dax"):
            if candidate not in excluded:
                return candidate
        return "voice-fallback"

    def synthesize(self, request: SpeechSynthesisRequest) -> SpeechSynthesisResult:
        with self._lock:
            self.requests.append(request)
            handler = self.handler
        return handler(request)


# ---------------------------------------------------------------------------
# Real-filesystem Theater + StoryEngine integration harness
# ---------------------------------------------------------------------------


@dataclass
class EngineBundle:
    theater: Theater
    canvas: CanvasStateManager
    lore_library: LoreLibrary
    reference_manager: ReferenceManager
    engine: StoryEngine
    audio_broadcasts: list[dict[str, JsonValue]] = field(default_factory=list)
    scene_reactions: list[dict[str, JsonValue]] = field(default_factory=list)


class StoryEngineIntegrationHarness:
    """Boots real StoryEngine instances backed by a real Theater on disk."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.manager = TheaterManager(root)
        self.text_provider = FakeTextResponseProvider(RESPONDER_MODEL)
        self.beautifier_text_provider = FakeTextResponseProvider(BEAUTIFIER_MODEL)
        self.decision_provider = FakeDecisionProvider()
        self.genai_client = FakeGenAIClient()
        self.speech_provider = FakeSpeechProvider()
        self.app_story_config: dict[str, JsonValue] = {}
        self.bundles: list[EngineBundle] = []

    def prepare_theater(
        self,
        theater_id: str = "vault",
        *,
        story_config: dict[str, JsonValue] | None = None,
        game_definition: GameDefinition | None = None,
        planning_schema: dict[str, JsonValue] | None = None,
    ) -> Theater:
        theater = self.manager.theater(theater_id)
        directory = theater.directory()
        directory.mkdir(parents=True, exist_ok=True)

        merged_story: dict[str, JsonValue] = {
            "adventure_mode": True,
            "enforce_structured": True,
            "require_user_input": False,
            "input_cycle_duration": 0,
            "output_cycle_duration": 0,
            "responder_model": RESPONDER_MODEL,
            "game_model": GAME_MODEL,
            "recorder_model": RECORDER_MODEL,
            "deep_planner_model": PLANNER_MODEL,
            "history_event_limit": 20,
            "enable_dice": True,
        }
        if story_config is not None:
            merged_story.update(story_config)

        # Keep operational app-level overrides aligned with what this test configured.
        self.app_story_config = {
            "responder_model": merged_story["responder_model"],
            "planner_model": merged_story["deep_planner_model"],
            "history_event_limit": merged_story["history_event_limit"],
        }
        if "history_chunk_size" in merged_story:
            self.app_story_config["history_chunk_size"] = merged_story["history_chunk_size"]
        if "deep_planning" in merged_story:
            self.app_story_config["deep_planning"] = merged_story["deep_planning"]

        config_doc: dict[str, JsonValue] = {
            "reference_generation_enabled": False,
            "story_planning": merged_story,
        }
        (directory / "theater.yaml").write_text(
            yaml.safe_dump(config_doc, sort_keys=False),
            encoding="utf-8",
        )

        schema = (
            planning_schema
            if planning_schema is not None
            else {
                "Inventory": {
                    "owner": "recorder",
                    "description": "Items currently carried by the player.",
                    "initial": "Rusty key",
                },
                "Location": {
                    "owner": "recorder",
                    "description": "Established room or area.",
                    "initial": "Outer Vault",
                },
                "Status": {
                    "owner": "recorder",
                    "description": "Structured condition of the player.",
                    "fields": {
                        "health": {"description": "Current physical condition", "initial": "100"},
                        "alert": {"description": "Current alertness level", "initial": "calm"},
                    },
                },
                "Ambush": {
                    "owner": "planner",
                    "description": "Conditional plans of the vault guardian.",
                    "initial": "Guardian watches from the upper gallery.",
                },
            }
        )
        (directory / "planning.yaml").write_text(
            yaml.safe_dump(schema, sort_keys=False),
            encoding="utf-8",
        )

        lore_dir = directory / "lore"
        lore_dir.mkdir(exist_ok=True)
        (lore_dir / "RESPONDER.md").write_text("RESPONDER_PRIVATE_GUIDE: Keep scenes crisp.", encoding="utf-8")
        (lore_dir / "RECORDER.md").write_text("RECORDER_PRIVATE_GUIDE: Record only verified facts.", encoding="utf-8")
        (lore_dir / "GAME.md").write_text("GAME_PRIVATE_GUIDE: Trade fairly under vault law.", encoding="utf-8")
        (lore_dir / "PLANNER.md").write_text("PLANNER_PRIVATE_GUIDE: Escalate tension off-screen.", encoding="utf-8")
        (lore_dir / "world.txt").write_text(
            "WORLD_LORE_FACT: The Obsidian Vault hums with lunar resonance.",
            encoding="utf-8",
        )
        (lore_dir / "vault_rules.txt").write_text(
            "VAULT_RULES_FACT: Every green relic offered must be logged in the ledger.",
            encoding="utf-8",
        )

        if game_definition is not None:
            game_dir = directory / "game"
            game_dir.mkdir(exist_ok=True)
            (game_dir / "game.json").write_text(
                game_definition.model_dump_json(),
                encoding="utf-8",
            )

        return theater

    def start_engine(
        self,
        theater: Theater,
        *,
        with_beautifier: bool = False,
        with_speech: bool = False,
    ) -> EngineBundle:
        beautifier = (
            TextBeautifier(text_provider=self.beautifier_text_provider)
            if with_beautifier
            else None
        )
        canvas = CanvasStateManager(theater, text_beautifier=beautifier)
        audio_broadcasts: list[dict[str, JsonValue]] = []
        if with_speech:
            canvas.story.publish_audio_fn = audio_broadcasts.append
            canvas.story.enable_scene_speech(
                self.speech_provider,
                character_voicing=True,
                narration_voicing=False,
            )
        lore_library = LoreLibrary(theater)
        reference_manager = ReferenceManager(
            theater,
            text_response_provider=self.text_provider,
            canvas_manager=canvas,
            speech_provider=self.speech_provider if with_speech else None,
            decision_provider=self.decision_provider,
            story_state=canvas.story,
        )
        engine = StoryEngine(
            theater=theater,
            canvas_manager=canvas,
            text_response_provider=self.text_provider,
            reference_manager=reference_manager,
            decision_provider=self.decision_provider,
            lore_library=lore_library,
            speech_provider=self.speech_provider if with_speech else None,
        )
        scene_reactions: list[dict[str, JsonValue]] = []
        engine.on_scene_reaction = scene_reactions.append
        bundle = EngineBundle(
            theater=theater,
            canvas=canvas,
            lore_library=lore_library,
            reference_manager=reference_manager,
            engine=engine,
            audio_broadcasts=audio_broadcasts,
            scene_reactions=scene_reactions,
        )
        self.bundles.append(bundle)
        return bundle

    def close_bundle(self, bundle: EngineBundle) -> None:
        bundle.engine.close()
        if bundle.canvas.story._speech_executor is not None:
            bundle.canvas.story._speech_executor.shutdown(wait=True)
        if bundle in self.bundles:
            self.bundles.remove(bundle)

    def close_all(self) -> None:
        for bundle in list(self.bundles):
            self.close_bundle(bundle)


@pytest.fixture
def integration_harness(tmp_path: Path) -> Iterator[StoryEngineIntegrationHarness]:
    harness = StoryEngineIntegrationHarness(tmp_path)

    def make_fake_client(**kwargs: str | bool | types.HttpOptions) -> FakeGenAIClient:
        return harness.genai_client

    def fake_app_config(config_filename: str = "app.yaml") -> dict[str, JsonValue]:
        return {"story_planning": dict(harness.app_story_config)}

    with (
        patch("components.story.story_models.genai.Client", side_effect=make_fake_client),
        patch("utils.config_loader.get_app_config", side_effect=fake_app_config),
    ):
        try:
            yield harness
        finally:
            harness.close_all()


# ---------------------------------------------------------------------------
# 1. Full end-to-end pipelines across all backends & real tools
# ---------------------------------------------------------------------------


def test_full_communicated_action_pipeline_exercises_all_backends_and_tools(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater(game_definition=exchange_definition())

    def decide(request: DecisionRequest) -> DecisionResult:
        answers: dict[str, DecisionAnswer] = {}
        for question in request.questions:
            if question.name == "should_take_game_action":
                answers[question.name] = PredicateAnswer(name=question.name, probability=0.95)
            elif question.name == "primary_lore_document":
                if "Committed Event" in request.input_text:
                    answers[question.name] = ChoiceAnswer(name=question.name, choice="vault_rules.txt")
                else:
                    answers[question.name] = ChoiceAnswer(name=question.name, choice="world.txt")
            else:
                answers[question.name] = ChoiceAnswer(name=question.name, choice="none")
        return DecisionResult(provider="fake-decision", model="fake-decision-model", answers=answers)

    integration_harness.decision_provider.handler = decide

    def responder(request: TextResponseRequest) -> TextResponseResult:
        assert "WORLD_LORE_FACT" in request.prompt
        assert "RESPONDER_PRIVATE_GUIDE" in (request.system_instruction or "")
        assert "RECORDER_PRIVATE_GUIDE" not in (request.system_instruction or "")
        assert "GAME_PRIVATE_GUIDE" not in (request.system_instruction or "")
        assert "PLANNER_PRIVATE_GUIDE" not in (request.system_instruction or "")
        assert "narratron_offer" not in request.prompt
        payload = {
            "narration": "The Vault Keeper slides a glowing emerald relic across the obsidian slab.",
            "dice_used": True,
            "dice_reason": "Gauge the Keeper's hidden terms",
            "dialogue": [
                {
                    "speaker": "Vault Keeper",
                    "text": "Inspect the green relic before we seal terms.",
                    "voice_instruction": "calm and resonant",
                }
            ],
            "manifested_characters": ["Vault Keeper"],
            "proposed_factual_changes": [
                {
                    "topic": "Location",
                    "info": "Inner Exchange Chamber",
                    "evidence": "Player stepped up to the obsidian slab.",
                }
            ],
        }
        return TextResponseResult(
            text=json.dumps(payload),
            provider="fake-text",
            model=RESPONDER_MODEL,
        )

    def game_controller(request: TextResponseRequest) -> TextResponseResult:
        assert "VAULT_RULES_FACT" in request.prompt
        assert "GAME_PRIVATE_GUIDE" in (request.system_instruction or "")
        decision = GameControlDecision(
            game_actions=(
                GameAction(name="narratron_place", arguments={"item": "green"}, revision=0),
            ),
            narration_request=None,
            reason="Keeper placed green relic on the table to match the scene.",
        )
        return TextResponseResult(
            text=decision.model_dump_json(),
            provider="fake-text",
            model=GAME_MODEL,
        )

    def beautifier(request: TextResponseRequest) -> TextResponseResult:
        return TextResponseResult(
            text=json.dumps({
                "narration_spans": [
                    {
                        "text": "The Vault Keeper slides a glowing emerald relic across the obsidian slab.",
                        "effect": "glow",
                        "font": "cinematic",
                        "color": None,
                    }
                ],
                "dialogue": [
                    {
                        "speaker": "Vault Keeper",
                        "text": "Inspect the green relic before we seal terms.",
                        "spans": [
                            {
                                "text": "Inspect the green relic before we seal terms.",
                                "effect": "none",
                                "font": "default",
                                "color": None,
                            }
                        ],
                    }
                ],
            }),
            provider="fake-text",
            model=BEAUTIFIER_MODEL,
        )

    integration_harness.text_provider.responder_handler = responder
    integration_harness.text_provider.game_handler = game_controller
    integration_harness.beautifier_text_provider.beautifier_handler = beautifier

    recorder_step = 0

    def recorder_llm(
        contents: list[types.Content],
        config: types.GenerateContentConfig | None,
    ) -> types.GenerateContentResponse:
        nonlocal recorder_step
        recorder_step += 1
        if recorder_step == 1:
            return make_genai_tool_call_response("check_schema", {"topic": "Status"}, "rec-1")
        if recorder_step == 2:
            assert "health" in flatten_genai_contents(contents)
            return make_genai_tool_call_response(
                "update_sticky_note",
                {"topic": "Status", "info": '{"health": "95", "alert": "watchful"}'},
                "rec-2",
            )
        if recorder_step == 3:
            assert "staged." in flatten_genai_contents(contents)
            return make_genai_tool_call_response("read_lore", {"document": "world.txt"}, "rec-3")
        return recorder_set_model_response(
            factual_updates={"Location": "Inner Exchange Chamber"},
            reference_updates=(
                CanonicalReferenceUpdate(
                    kind="character",
                    name="Archivist Ren",
                    description="Cloaked custodian of obsidian relics.",
                    personality="Measured and exacting",
                    motivation="Preserve vault balance",
                    quirk="Taps silver rings on stone",
                    gender="nonbinary",
                    voice_tags=["nonbinary"],
                ),
            ),
            reason="Recorded chamber entry, status, and Archivist Ren profile.",
            call_id="rec-4",
        )

    planner_step = 0

    def planner_llm(
        contents: list[types.Content],
        config: types.GenerateContentConfig | None,
    ) -> types.GenerateContentResponse:
        nonlocal planner_step
        planner_step += 1
        if planner_step == 1:
            return make_genai_tool_call_response("deep_read_lore", {"document": "vault_rules.txt"}, "pln-1")
        if planner_step == 2:
            return make_genai_tool_call_response(
                "roll_dice",
                {"sides": 20, "count": 1, "modifier": 0, "reason": "Guardian patience"},
                "pln-2",
            )
        if planner_step == 3:
            return make_genai_tool_call_response(
                "update_sticky_note",
                {"topic": "Ambush", "info": "Keeper prepares a counter-demand if player stalls."},
                "pln-3",
            )
        return make_genai_text_response("Planner updated ambush contingency.")

    integration_harness.genai_client.recorder_handler = recorder_llm
    integration_harness.genai_client.planner_handler = planner_llm

    bundle = integration_harness.start_engine(theater, with_beautifier=True, with_speech=True)
    result = bundle.engine.resolve_action("Approach the slab and examine the Keeper's offer", drain=True)

    assert "error" not in result
    assert result["narration"] == "The Vault Keeper slides a glowing emerald relic across the obsidian slab."
    assert len(result["die_rolls"]) == 1

    # Canvas narration, beautifier spans, dialogue, and speech synthesis
    assert bundle.canvas.story.narration == result["narration"]
    assert bundle.canvas.story.narration_spans[0]["effect"] == "glow"
    assert bundle.canvas.story.scene_dialogue[0]["speaker"] == "Vault Keeper"
    assert bundle.canvas.story.last_die_roll is not None
    if bundle.canvas.story._speech_executor is not None:
        bundle.canvas.story._speech_executor.shutdown(wait=True)
        bundle.canvas.story._speech_executor = None
    assert len(bundle.audio_broadcasts) == 1
    assert bundle.audio_broadcasts[0]["speaker"] == "Vault Keeper"
    assert bundle.audio_broadcasts[0]["speech_generation"] == bundle.canvas.story.committed_scene_speech_generation

    # Game runtime state & receipts
    assert bundle.canvas.game is not None
    assert bundle.canvas.game.revision == 1
    assert bundle.canvas.game.state["narratron_offer"] == ["green"]
    turns = bundle.engine.turn_journal.turns()
    assert len(turns) == 1 and turns[0].completed
    assert len(bundle.engine.recording_journal.narratron_receipts(f"{turns[0].turn_id}:recording")) == 1

    # Notepad reflects both recorder-owned (plain + structured) and planner-owned updates
    notes = bundle.engine.notepad.snapshot().notes
    assert notes["Location"] == "Inner Exchange Chamber"
    assert "95" in notes["Status"] and "watchful" in notes["Status"]
    assert notes["Ambush"] == "Keeper prepares a counter-demand if player stalls."

    # ReferenceManager created canonical NPC & synced voice tags
    characters = bundle.reference_manager.export_characters()
    assert [c.name for c in characters] == ["Archivist Ren"]
    assert bundle.canvas.story.get_character_voice_tags("Archivist Ren") == ["nonbinary"]

    # History contains responder/game/recorder lore reads, player action, narratron game action, story output
    # but NOT planner's deep_read_lore.
    history_events = bundle.engine.recording_journal.recent_history(20)
    kinds = [ev.kind for ev in history_events]
    assert "player_action" in kinds
    assert "game_action" in kinds
    assert "story_output" in kinds
    lore_readers = [str(ev.payload["reader"]) for ev in history_events if ev.kind == "lore_read"]
    assert "planner" not in lore_readers
    assert "responder" in lore_readers
    assert "game" in lore_readers
    assert "recorder" in lore_readers


def test_full_game_action_pipeline_with_narration_and_skip(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater(game_definition=exchange_definition())

    game_call_index = 0

    def game_controller(request: TextResponseRequest) -> TextResponseResult:
        nonlocal game_call_index
        game_call_index += 1
        if game_call_index == 1:
            decision = GameControlDecision(
                game_actions=(
                    GameAction(name="narratron_place", arguments={"item": "green"}, revision=1),
                ),
                narration_request=NarrationRequest(
                    user_action="Player placed the red relic on the exchange table.",
                    brief="The Keeper answers the red relic by setting down a green relic.",
                ),
                reason="Opening offer warrants dramatic narration.",
            )
        else:
            decision = GameControlDecision(
                game_actions=(),
                narration_request=None,
                reason="Second minor placement needs no narration.",
            )
        return TextResponseResult(text=decision.model_dump_json(), provider="fake-text", model=GAME_MODEL)

    def responder(request: TextResponseRequest) -> TextResponseResult:
        assert "The Keeper answers the red relic" in request.prompt
        assert "player_inventory" not in request.prompt
        assert "Dice rolling is disabled." in (request.system_instruction or "")
        return TextResponseResult(
            text=json.dumps({"narration": "Red and green relics gleam across the obsidian table."}),
            provider="fake-text",
            model=RESPONDER_MODEL,
        )

    integration_harness.text_provider.game_handler = game_controller
    integration_harness.text_provider.responder_handler = responder

    rec_calls = 0

    def recorder_llm(
        contents: list[types.Content],
        config: types.GenerateContentConfig | None,
    ) -> types.GenerateContentResponse:
        nonlocal rec_calls
        rec_calls += 1
        if rec_calls == 1:
            assert "narratron_place" in flatten_genai_contents(contents)
            return recorder_set_model_response(
                factual_updates={"Inventory": "Blue relic (red on table)"},
                reason="Recorded opening exchange.",
                call_id="rec-game-1",
            )
        return recorder_set_model_response(
            factual_updates={"Inventory": "No relics in pocket (red and blue on table)"},
            reason="Recorded second placement.",
            call_id="rec-game-2",
        )

    integration_harness.genai_client.recorder_handler = recorder_llm

    bundle = integration_harness.start_engine(theater)
    assert bundle.canvas.game is not None

    # Move 1: Narrated game action + Narratron counter-action
    bundle.canvas.game.act(GameAction(name="player_place", arguments={"item": "red"}, revision=0))
    assert bundle.engine.wait_for_pending_events(10.0)
    assert bundle.canvas.game.revision == 2
    assert bundle.canvas.game.state["player_offer"] == ["red"]
    assert bundle.canvas.game.state["narratron_offer"] == ["green"]
    assert bundle.canvas.story.narration == "Red and green relics gleam across the obsidian table."
    assert len(integration_harness.text_provider.calls_for("responder")) == 1

    # Move 2: Narration-skipped game action
    bundle.canvas.game.act(GameAction(name="player_place", arguments={"item": "blue"}, revision=2))
    assert bundle.engine.wait_for_pending_events(10.0)
    assert bundle.canvas.game.revision == 3
    assert len(integration_harness.text_provider.calls_for("responder")) == 1
    assert bundle.canvas.story.narration == "Red and green relics gleam across the obsidian table."
    assert bundle.engine.notepad.snapshot().notes["Inventory"] == "No relics in pocket (red and blue on table)"

    turns = bundle.engine.turn_journal.turns()
    assert len(turns) == 2
    assert not turns[0].responder_skipped and turns[0].completed
    assert turns[1].responder_skipped and turns[1].completed


# ---------------------------------------------------------------------------
# 2. API backend failures, timeouts, malformed responses, & recovery
# ---------------------------------------------------------------------------


def test_decision_provider_failure_at_responder_lore_and_game_gate_recovers_cleanly(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater(game_definition=exchange_definition())

    fail_mode: dict[str, str] = {"stage": "responder_lore"}

    def decide(request: DecisionRequest) -> DecisionResult:
        names = {q.name for q in request.questions}
        if fail_mode["stage"] == "responder_lore" and "primary_lore_document" in names:
            raise DecisionProviderError("Decision API 503 during lore selection")
        if fail_mode["stage"] == "game_gate" and "should_take_game_action" in names:
            raise DecisionProviderError("Decision API timeout during game gate")
        answers: dict[str, DecisionAnswer] = {}
        for q in request.questions:
            if q.question_type == "predicate":
                answers[q.name] = PredicateAnswer(name=q.name, probability=0.05)
            else:
                answers[q.name] = ChoiceAnswer(name=q.name, choice="none")
        return DecisionResult(provider="fake-decision", model="fake-decision-model", answers=answers)

    integration_harness.decision_provider.handler = decide
    bundle = integration_harness.start_engine(theater)

    # Phase A: Fail at responder lore pre-selection
    res1 = bundle.engine.resolve_action("Check the vault inscription")
    assert "error" in res1
    assert "Decision API 503" in str(res1["error"])
    assert not bundle.engine.is_action_in_flight
    assert bundle.canvas.story.narration == ""
    assert len(integration_harness.text_provider.calls_for("responder")) == 0

    # Phase B: Recover lore selection, fail at background game gate after responder succeeds
    fail_mode["stage"] = "game_gate"
    bundle.engine.retry_pending_turns()
    assert not bundle.engine.wait_for_pending_events(10.0)
    # Scene was already delivered to canvas even though background game gate failed!
    assert bundle.canvas.story.narration == "The brass lock yields with a crisp click."
    assert len(integration_harness.text_provider.calls_for("responder")) == 1
    turn = bundle.engine.turn_journal.turns()[0]
    assert turn.response is not None and turn.delivered and not turn.recorder_completed

    # Phase C: Recover game gate -> retries recording without re-calling responder
    fail_mode["stage"] = "healthy"
    bundle.engine.retry_pending_turns()
    assert bundle.engine.wait_for_pending_events(10.0)
    assert len(integration_harness.text_provider.calls_for("responder")) == 1
    assert bundle.engine.turn_journal.turns()[0].completed
    assert bundle.engine.notepad.snapshot().notes["Inventory"] == "Brass key"


def test_responder_text_api_failures_and_timeout_leave_engine_recoverable(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater()
    late_release = Event()
    late_entered = Event()
    attempt = 0

    def responder(request: TextResponseRequest) -> TextResponseResult:
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise TextResponseProviderError("503 Responder API unavailable")
        if attempt == 2:
            return TextResponseResult(
                text="{not valid json",
                provider="fake-text",
                model=RESPONDER_MODEL,
            )
        if attempt == 3:
            late_entered.set()
            late_release.wait(5.0)
            return TextResponseResult(
                text=json.dumps({"narration": "LATE_STALE_OUTPUT_MUST_NOT_APPEAR"}),
                provider="fake-text",
                model=RESPONDER_MODEL,
            )
        return TextResponseResult(
            text=json.dumps({"narration": "Fresh recovered narration after transient failures."}),
            provider="fake-text",
            model=RESPONDER_MODEL,
        )

    integration_harness.text_provider.responder_handler = responder
    bundle = integration_harness.start_engine(theater)
    bundle.engine.response_module.user_action_timeout_seconds = 0.15

    try:
        # Attempt 1: 503 error
        err1 = bundle.engine.resolve_action("Push the heavy bronze door")
        assert "error" in err1
        assert "503" in str(err1["error"])
        assert bundle.canvas.story.narration == ""

        # Attempt 2: malformed JSON
        bundle.engine.retry_pending_turns()
        assert not bundle.engine.wait_for_pending_events(10.0)
        assert bundle.canvas.story.narration == ""

        # Attempt 3: timeout with late completion
        bundle.engine.retry_pending_turns()
        assert late_entered.wait(5.0)
        assert not bundle.engine.wait_for_pending_events(10.0)
        assert bundle.canvas.story.narration == ""
        late_release.set()
        time.sleep(0.05)
        assert "LATE_STALE_OUTPUT" not in bundle.canvas.story.narration
        assert bundle.engine.turn_journal.turns()[0].response is None

        # Attempt 4: healthy recovery
        bundle.engine.retry_pending_turns()
        assert bundle.engine.wait_for_pending_events(10.0)
        assert bundle.canvas.story.narration == "Fresh recovered narration after transient failures."
        assert bundle.engine.turn_journal.turns()[0].completed
        assert len(bundle.engine.turn_journal.turns()) == 1
    finally:
        late_release.set()


def test_game_module_invalid_action_corrective_retry_and_timeout_recovery(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater(game_definition=exchange_definition())

    timeout_entered = Event()
    timeout_release = Event()
    game_attempt = 0
    keep_game_down = False

    def game_controller(request: TextResponseRequest) -> TextResponseResult:
        nonlocal game_attempt
        game_attempt += 1
        if game_attempt == 1:
            # Invalid action: wrong item not in narratron_inventory -> game runtime rejects!
            bad = GameControlDecision(
                game_actions=(
                    GameAction(name="narratron_place", arguments={"item": "nonexistent_ruby"}, revision=1),
                ),
                narration_request=None,
                reason="Attempted invalid ruby placement.",
            )
            return TextResponseResult(text=bad.model_dump_json(), provider="fake-text", model=GAME_MODEL)
        if game_attempt == 2:
            # Corrective retry must include rejection feedback in prompt!
            assert "Rejected Narratron game actions" in request.prompt
            assert "nonexistent_ruby" in request.prompt
            good = GameControlDecision(
                game_actions=(
                    GameAction(name="narratron_place", arguments={"item": "green"}, revision=1),
                ),
                narration_request=None,
                reason="Corrected to available green relic.",
            )
            return TextResponseResult(text=good.model_dump_json(), provider="fake-text", model=GAME_MODEL)
        if game_attempt == 3:
            timeout_entered.set()
            timeout_release.wait(5.0)
            return TextResponseResult(
                text=GameControlDecision(reason="Late game output").model_dump_json(),
                provider="fake-text",
                model=GAME_MODEL,
            )
        if keep_game_down:
            raise TextResponseProviderError("Game API still unavailable")
        recovered = GameControlDecision(
            game_actions=(GameAction(name="narratron_finalize", arguments={}, revision=3),),
            narration_request=None,
            reason="Recovered after timeout and finalized offer.",
        )
        return TextResponseResult(text=recovered.model_dump_json(), provider="fake-text", model=GAME_MODEL)

    integration_harness.text_provider.game_handler = game_controller
    bundle = integration_harness.start_engine(theater)
    bundle.engine.game_module.game_action_timeout_seconds = 0.15
    assert bundle.canvas.game is not None

    try:
        # Turn 1: Invalid action rejected on attempt 1, corrected automatically on attempt 2!
        bundle.canvas.game.act(GameAction(name="player_place", arguments={"item": "red"}, revision=0))
        assert bundle.engine.wait_for_pending_events(10.0)
        assert game_attempt == 2
        assert bundle.canvas.game.revision == 2
        assert bundle.canvas.game.state["narratron_offer"] == ["green"]

        # Turn 2: Game API times out -> blocks subsequent spoken action while game API stays down
        keep_game_down = True
        bundle.canvas.game.act(GameAction(name="player_place", arguments={"item": "blue"}, revision=2))
        assert timeout_entered.wait(5.0)
        assert not bundle.engine.wait_for_pending_events(10.0)
        blocked = bundle.engine.resolve_action("Ask if the trade is final")
        assert blocked == {"error": "An earlier story cycle requires recovery."}
        timeout_release.set()

        # Retry recovers both Turn 2 (game) and Turn 3 (blocked spoken action) cleanly!
        keep_game_down = False
        bundle.engine.retry_pending_turns()
        assert bundle.engine.wait_for_pending_events(10.0)
        assert bundle.canvas.game.revision == 4
        assert bundle.canvas.game.state["narratron_done"] is True
        assert all(turn.completed for turn in bundle.engine.turn_journal.turns())
        assert len(bundle.engine.turn_journal.turns()) == 3
    finally:
        timeout_release.set()


def test_adk_recorder_malformed_function_calls_and_429_rate_limit_recovery(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater()

    recorder_phase: dict[str, int] = {"mode": 1, "calls_in_mode": 0}

    def recorder_llm(
        contents: list[types.Content],
        config: types.GenerateContentConfig | None,
    ) -> types.GenerateContentResponse:
        recorder_phase["calls_in_mode"] += 1
        count = recorder_phase["calls_in_mode"]
        mode = recorder_phase["mode"]

        if mode == 1:
            # Two MALFORMED_FUNCTION_CALL responses -> VertexGemini retries internally (max 3 attempts)
            if count <= 2:
                return make_genai_malformed_response()
            return recorder_set_model_response(
                factual_updates={"Inventory": "Key recovered via VertexGemini retry"},
                reason="Succeeded on 3rd VertexGemini attempt.",
                call_id="rec-m1",
            )
        if mode == 2:
            # Four MALFORMED_FUNCTION_CALL responses -> exhausts VertexGemini (3), triggers StorySession attempt 2!
            if count <= 4:
                return make_genai_malformed_response()
            return recorder_set_model_response(
                factual_updates={"Inventory": "Key recovered via StorySession retry"},
                reason="Succeeded on StorySession 2nd invocation.",
                call_id="rec-m2",
            )
        if mode == 3:
            # Rate-limited with real google.genai.errors.ClientError(429) -> ADK wraps as _ResourceExhaustedError
            raise make_rate_limit_error("Vertex AI 429 rate limit")
        return recorder_set_model_response(
            factual_updates={"Inventory": f"Recorded after 429 recovery #{count}"},
            reason="Recovered after rate limit cleared.",
            call_id=f"rec-m4-{count}",
        )

    integration_harness.genai_client.recorder_handler = recorder_llm
    bundle = integration_harness.start_engine(theater)

    # Mode 1: VertexGemini retries 2 malformed calls transparently inside a single turn
    res1 = bundle.engine.resolve_action("Turn the first dial", drain=True)
    assert "error" not in res1
    assert bundle.engine.notepad.snapshot().notes["Inventory"] == "Key recovered via VertexGemini retry"
    assert recorder_phase["calls_in_mode"] == 3

    # Mode 2: StorySession retries after VertexGemini exhausts all 3 attempts
    recorder_phase["mode"] = 2
    recorder_phase["calls_in_mode"] = 0
    res2 = bundle.engine.resolve_action("Turn the second dial", drain=True)
    assert "error" not in res2
    assert bundle.engine.notepad.snapshot().notes["Inventory"] == "Key recovered via StorySession retry"
    assert recorder_phase["calls_in_mode"] == 5

    # Mode 3: 429 rate limit fails Turn 3 recorder and blocks Turn 4 until cleared
    recorder_phase["mode"] = 3
    recorder_phase["calls_in_mode"] = 0
    res3 = bundle.engine.resolve_action("Turn the third dial")
    assert "error" in res3
    assert "429" in str(res3["error"]) or "RESOURCE_EXHAUSTED" in str(res3["error"])
    responder_calls_before_block = len(integration_harness.text_provider.calls_for("responder"))

    # Dependent Turn 4 is blocked while Turn 3 recorder is incomplete
    res4 = bundle.engine.resolve_action("Pull the vault lever")
    assert "error" in res4
    assert len(integration_harness.text_provider.calls_for("responder")) == responder_calls_before_block

    # Mode 4: Rate limit clears -> retry completes Turn 3 (without re-running responder!) and then Turn 4
    recorder_phase["mode"] = 4
    recorder_phase["calls_in_mode"] = 0
    bundle.engine.retry_pending_turns()
    assert bundle.engine.wait_for_pending_events(10.0)
    assert len(integration_harness.text_provider.calls_for("responder")) == responder_calls_before_block + 1
    assert all(turn.completed for turn in bundle.engine.turn_journal.turns())
    assert bundle.engine.recording_journal.pending() == []


def test_adk_recorder_unauthorized_write_tool_error_and_atomic_rejection(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater()

    phase: dict[str, int] = {"turn": 1, "step": 0}

    def recorder_llm(
        contents: list[types.Content],
        config: types.GenerateContentConfig | None,
    ) -> types.GenerateContentResponse:
        phase["step"] += 1
        if phase["turn"] == 1:
            if phase["step"] == 1:
                # Attempt to write a planner-owned topic via tool -> StagedStoryNotes returns Error
                return make_genai_tool_call_response(
                    "update_sticky_note",
                    {"topic": "Ambush", "info": "Unauthorized recorder overwrite"},
                    "bad-tool-1",
                )
            if phase["step"] == 2:
                # Model observes real tool error in continuation and writes its own topic instead
                assert "cannot write" in flatten_genai_contents(contents)
                return make_genai_tool_call_response(
                    "update_sticky_note",
                    {"topic": "Inventory", "info": "Verified lantern"},
                    "good-tool-2",
                )
            return recorder_set_model_response(
                reason="Corrected to recorder-owned Inventory.",
                call_id="rec-t1-done",
            )
        if phase["turn"] == 2:
            # Return unauthorized planner topic directly in structured output -> whole batch must fail atomically
            return recorder_set_model_response(
                factual_updates={"Inventory": "Atomic rollback test", "Ambush": "Illegal hijack"},
                reason="Illegal mixed write.",
                call_id="rec-t2-bad",
            )
        return recorder_set_model_response(
            factual_updates={"Inventory": "Clean recovered lantern"},
            reason="Valid recorder-only update.",
            call_id="rec-t2-good",
        )

    integration_harness.genai_client.recorder_handler = recorder_llm
    bundle = integration_harness.start_engine(theater)

    # Turn 1: Tool-level rejection recovered within the same ADK invocation
    res1 = bundle.engine.resolve_action("Light the vault lantern", drain=True)
    assert "error" not in res1
    assert bundle.engine.notepad.snapshot().notes["Inventory"] == "Verified lantern"
    assert bundle.engine.notepad.snapshot().notes["Ambush"] == "Guardian watches from the upper gallery."

    # Turn 2: Structured-output ownership violation rolls back atomically (Inventory unchanged!)
    phase["turn"] = 2
    phase["step"] = 0
    res2 = bundle.engine.resolve_action("Inspect the shadows")
    assert "error" in res2
    assert "cannot write" in str(res2["error"])
    assert bundle.engine.notepad.snapshot().notes["Inventory"] == "Verified lantern"
    assert bundle.engine.notepad.snapshot().notes["Ambush"] == "Guardian watches from the upper gallery."

    # Recovery with valid recorder-only update
    phase["turn"] = 3
    phase["step"] = 0
    bundle.engine.retry_pending_turns()
    assert bundle.engine.wait_for_pending_events(10.0)
    assert bundle.engine.notepad.snapshot().notes["Inventory"] == "Clean recovered lantern"
    assert all(turn.completed for turn in bundle.engine.turn_journal.turns())


def test_adk_planner_failures_never_block_foreground_and_coalesce_backlog(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater(
        story_config={"history_event_limit": 2, "history_chunk_size": 1},
    )

    planner_online = False
    planner_step = 0
    captured_planner_prompts: list[str] = []

    def planner_llm(
        contents: list[types.Content],
        config: types.GenerateContentConfig | None,
    ) -> types.GenerateContentResponse:
        nonlocal planner_step
        if not planner_online:
            raise make_server_error("503 Planner backend down")
        captured_planner_prompts.append(flatten_genai_contents(contents))
        planner_step += 1
        if planner_step == 1:
            return make_genai_tool_call_response(
                "update_sticky_note",
                {"topic": "Ambush", "info": "Coalesced plan from newest state."},
                "pln-coalesce-1",
            )
        return make_genai_text_response("Coalesced planning finished.")

    integration_harness.genai_client.planner_handler = planner_llm
    bundle = integration_harness.start_engine(theater)

    # Run 3 foreground turns while planner backend continuously fails with 503
    for action in ("ALPHA_OLDEST_ACTION", "BETA_MIDDLE_ACTION", "GAMMA_NEWEST_ACTION"):
        result = bundle.engine.resolve_action(action)
        assert "error" not in result
        assert bundle.engine.cycle.wait(5.0)
        assert not bundle.engine.wait_for_deep_planning(5.0)

    assert len(bundle.engine.turn_journal.turns()) == 3
    assert all(turn.completed for turn in bundle.engine.turn_journal.turns())
    responder_count = len(integration_harness.text_provider.calls_for("responder"))
    recorder_count = len(integration_harness.genai_client.calls_for("recorder"))

    # Bring planner back online and retry -> no foreground stage repeats, backlog is coalesced
    planner_online = True
    bundle.engine.retry_pending_turns()
    assert bundle.engine.wait_for_pending_events(10.0)

    assert len(integration_harness.text_provider.calls_for("responder")) == responder_count
    assert len(integration_harness.genai_client.calls_for("recorder")) == recorder_count
    assert bundle.engine.notepad.snapshot().notes["Ambush"] == "Coalesced plan from newest state."
    assert captured_planner_prompts
    assert "ALPHA_OLDEST_ACTION" not in captured_planner_prompts[0]
    assert "GAMMA_NEWEST_ACTION" in captured_planner_prompts[0]


def test_beautifier_and_speech_backend_failures_and_supersession_do_not_break_engine(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater()

    beautify_mode: dict[str, str] = {"mode": "fail"}
    speech_mode: dict[str, str] = {"mode": "fail"}
    slow_speech_started = Event()
    slow_speech_release = Event()

    def beautify(request: TextResponseRequest) -> TextResponseResult:
        if beautify_mode["mode"] == "fail":
            raise TextResponseProviderError("Beautifier 500")
        return TextResponseResult(
            text=json.dumps({
                "narration_spans": [{"text": "Beautified.", "effect": "pulse", "font": "cinematic", "color": None}],
                "dialogue": [{"spans": [{"text": "Spoken line.", "effect": "none", "font": "default", "color": None}]}],
            }),
            provider="fake-text",
            model=BEAUTIFIER_MODEL,
        )

    def synthesize(request: SpeechSynthesisRequest) -> SpeechSynthesisResult:
        if speech_mode["mode"] == "fail":
            raise SpeechProviderError("TTS quota exceeded")
        if "STALE_SPEECH_LINE" in request.text:
            slow_speech_started.set()
            slow_speech_release.wait(5.0)
        return SpeechSynthesisResult(
            audio_bytes=f"AUDIO:{request.text}".encode("utf-8"),
            mime_type="audio/mpeg",
            provider="fake-speech",
            model="fake-speech-model",
        )

    turn_num = 0

    def responder(request: TextResponseRequest) -> TextResponseResult:
        nonlocal turn_num
        turn_num += 1
        if turn_num == 1:
            payload = {
                "narration": "Plain fallback scene when beautifier and TTS fail.",
                "dialogue": [{"speaker": "Mira", "text": "Can you hear me?"}],
            }
        elif turn_num == 2:
            payload = {
                "narration": "Scene two starts slow TTS.",
                "dialogue": [{"speaker": "Mira", "text": "STALE_SPEECH_LINE"}],
            }
        else:
            payload = {
                "narration": "Scene three supersedes scene two speech.",
                "dialogue": [{"speaker": "Mira", "text": "FRESH_SPEECH_LINE"}],
            }
        return TextResponseResult(text=json.dumps(payload), provider="fake-text", model=RESPONDER_MODEL)

    integration_harness.text_provider.responder_handler = responder
    integration_harness.beautifier_text_provider.beautifier_handler = beautify
    integration_harness.speech_provider.handler = synthesize

    bundle = integration_harness.start_engine(theater, with_beautifier=True, with_speech=True)

    try:
        # Turn 1: Both beautifier & TTS fail -> scene still commits to canvas cleanly!
        res1 = bundle.engine.resolve_action("Call out into the hall", drain=True)
        assert "error" not in res1
        assert bundle.canvas.story.narration == "Plain fallback scene when beautifier and TTS fail."
        assert bundle.canvas.story.scene_dialogue[0]["text"] == "Can you hear me?"
        assert bundle.engine.turn_journal.turns()[0].completed

        # Turn 2 -> Turn 3: Scene 2 TTS is slow, Scene 3 supersedes it -> Scene 2 audio must be discarded!
        beautify_mode["mode"] = "ok"
        speech_mode["mode"] = "ok"
        res2 = bundle.engine.resolve_action("Whisper to Mira", drain=True)
        assert "error" not in res2
        assert slow_speech_started.wait(5.0)

        res3 = bundle.engine.resolve_action("Shout across the vault", drain=True)
        assert "error" not in res3
        slow_speech_release.set()

        if bundle.canvas.story._speech_executor is not None:
            bundle.canvas.story._speech_executor.shutdown(wait=True)
            bundle.canvas.story._speech_executor = None

        assert bundle.canvas.story.narration == "Scene three supersedes scene two speech."
        # Only FRESH_SPEECH_LINE audio should have been broadcast for the latest generation.
        broadcast_generations = [int(msg["speech_generation"]) for msg in bundle.audio_broadcasts]
        assert broadcast_generations == [bundle.canvas.story.committed_scene_speech_generation]
    finally:
        slow_speech_release.set()


# ---------------------------------------------------------------------------
# 3. Timing, concurrency, batching, dwell, budgets, and restart recovery
# ---------------------------------------------------------------------------


def test_slow_background_recorder_delivers_canvas_immediately_but_serializes_next_responder(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater()

    rec1_started = Event()
    rec1_release = Event()
    resp2_started = Event()
    resp2_prompts: list[str] = []

    resp_calls = 0

    def responder(request: TextResponseRequest) -> TextResponseResult:
        nonlocal resp_calls
        resp_calls += 1
        if resp_calls == 1:
            return TextResponseResult(
                text=json.dumps({"narration": "You forge the rusty key into a silver key."}),
                provider="fake-text",
                model=RESPONDER_MODEL,
            )
        resp2_prompts.append(request.prompt)
        resp2_started.set()
        return TextResponseResult(
            text=json.dumps({"narration": "The silver key unlocks the inner sanctum."}),
            provider="fake-text",
            model=RESPONDER_MODEL,
        )

    rec_calls = 0

    def recorder_llm(
        contents: list[types.Content],
        config: types.GenerateContentConfig | None,
    ) -> types.GenerateContentResponse:
        nonlocal rec_calls
        rec_calls += 1
        if rec_calls == 1:
            rec1_started.set()
            assert rec1_release.wait(10.0)
            return recorder_set_model_response(
                factual_updates={"Inventory": "Silver Key"},
                reason="Upgraded key.",
                call_id="rec-slow-1",
            )
        return recorder_set_model_response(
            factual_updates={"Location": "Inner Sanctum"},
            reason="Entered sanctum.",
            call_id="rec-slow-2",
        )

    integration_harness.text_provider.responder_handler = responder
    integration_harness.genai_client.recorder_handler = recorder_llm

    bundle = integration_harness.start_engine(theater)

    try:
        bundle.engine.process_user_action("Reforge the rusty key")
        assert rec1_started.wait(5.0)
        # Canvas already updated immediately before slow ADK recorder finished!
        assert bundle.canvas.story.narration == "You forge the rusty key into a silver key."

        # Submit Turn 2 while Turn 1 ADK recorder is still blocked
        bundle.engine.process_user_action("Unlock the inner sanctum")
        assert not resp2_started.wait(0.15)

        # Release Turn 1 recorder -> Turn 2 responder unblocks and MUST see "Silver Key"!
        rec1_release.set()
        assert resp2_started.wait(5.0)
        assert bundle.engine.wait_for_pending_events(10.0)

        assert resp2_prompts
        assert "Silver Key" in resp2_prompts[0]
        assert "Rusty key" not in resp2_prompts[0]
        assert bundle.engine.notepad.snapshot().notes["Location"] == "Inner Sanctum"
    finally:
        rec1_release.set()


def test_input_batching_and_output_dwell_supersession_under_real_timers(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    # Real non-zero timers (short enough for fast deterministic integration runs)
    theater = integration_harness.prepare_theater(
        story_config={
            "input_cycle_duration": 0.25,
            "output_cycle_duration": 0.35,
        }
    )

    resp1_started = Event()
    resp1_release = Event()
    responder_prompts: list[str] = []

    def responder(request: TextResponseRequest) -> TextResponseResult:
        responder_prompts.append(request.prompt)
        index = len(responder_prompts)
        if index == 1:
            resp1_started.set()
            assert resp1_release.wait(5.0)
            narration = "SCENE_ONE_FIRST"
        elif index == 2:
            narration = "SCENE_TWO_COMBINED"
        else:
            narration = f"SCENE_{index}"
        return TextResponseResult(
            text=json.dumps({"narration": narration}),
            provider="fake-text",
            model=RESPONDER_MODEL,
        )

    integration_harness.text_provider.responder_handler = responder
    bundle = integration_harness.start_engine(theater)

    try:
        # Action 1 starts immediately and holds responder open briefly
        bundle.engine.process_user_action("STEP_ONE")
        assert resp1_started.wait(5.0)

        # Actions 2 & 3 arrive while Action 1 is active -> gathered into ONE batch!
        bundle.engine.process_user_action("STEP_TWO")
        bundle.engine.process_user_action("STEP_THREE")
        resp1_release.set()

        # Wait until scene 1 is displayed immediately, then wait for batch 2 to fire naturally via timer
        assert bundle.engine.wait_for_pending_events(10.0)

        assert len(responder_prompts) == 2
        assert "STEP_ONE" in responder_prompts[0]
        assert "STEP_TWO" in responder_prompts[1]
        assert "STEP_THREE" in responder_prompts[1]

        # Monotonic output display reached the latest scene
        assert bundle.canvas.story.narration == "SCENE_TWO_COMBINED"
        turns = bundle.engine.turn_journal.turns()
        assert len(turns) == 3
        assert turns[1].cycle_id == turns[2].cycle_id
        assert turns[0].delivered and turns[1].delivered
        assert turns[2].response_owner_id == turns[1].turn_id
        assert all(turn.completed and (turn.response is None or turn.delivered) for turn in turns)
    finally:
        resp1_release.set()


def test_output_dwell_supersedes_intermediate_scene_and_starts_after_beautification(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater(
        story_config={
            "input_cycle_duration": 0.0,
            "output_cycle_duration": 60.0,
        }
    )

    beautify_started = Event()
    beautify_release = Event()
    beautify_calls: list[str] = []

    def beautify(request: TextResponseRequest) -> TextResponseResult:
        beautify_calls.append(request.prompt)
        if len(beautify_calls) == 1:
            beautify_started.set()
            assert beautify_release.wait(5.0)
        return TextResponseResult(
            text=json.dumps({
                "narration_spans": [{"text": "Styled.", "effect": "glow", "font": "cinematic", "color": None}],
                "dialogue": [],
            }),
            provider="fake-text",
            model=BEAUTIFIER_MODEL,
        )

    seq = 0

    def responder(request: TextResponseRequest) -> TextResponseResult:
        nonlocal seq
        seq += 1
        return TextResponseResult(
            text=json.dumps({"narration": f"SCENE_OUTPUT_{seq}"}),
            provider="fake-text",
            model=RESPONDER_MODEL,
        )

    integration_harness.text_provider.responder_handler = responder
    integration_harness.beautifier_text_provider.beautifier_handler = beautify

    with patch("components.story.output_display_cycle.Timer"):
        bundle = integration_harness.start_engine(theater, with_beautifier=True)
        try:
            # Turn 1 blocks inside TextBeautifier before canvas state mutates
            bundle.engine.process_user_action("Action 1")
            assert beautify_started.wait(5.0)
            assert bundle.canvas.story.narration == ""
            beautify_release.set()
            assert bundle.engine.cycle.wait(5.0)
            assert bundle.canvas.story.narration == "SCENE_OUTPUT_1"

            # Turns 2 and 3 finish during Turn 1's output dwell: Turn 3 supersedes waiting Turn 2!
            assert bundle.engine.resolve_action("Action 2")["narration"] == "SCENE_OUTPUT_2"
            assert bundle.engine.resolve_action("Action 3")["narration"] == "SCENE_OUTPUT_3"
            # Beautifier was NOT called for waiting/superseded SCENE_OUTPUT_2!
            assert len(beautify_calls) == 1
            assert bundle.canvas.story.narration == "SCENE_OUTPUT_1"

            # Flushing output cycle promotes SCENE_OUTPUT_3 directly (skipping SCENE_OUTPUT_2)
            assert bundle.engine.wait_for_pending_events(10.0)
            assert bundle.canvas.story.narration == "SCENE_OUTPUT_3"
            assert len(beautify_calls) == 2
            assert "SCENE_OUTPUT_3" in beautify_calls[1]
            assert "SCENE_OUTPUT_2" not in beautify_calls[1]
            assert bundle.canvas.story.output_cycle.payload()["displayed_sequence"] == 3
        finally:
            beautify_release.set()


def test_slow_planner_assimilates_mid_invocation_updates_and_retries_stale_snapshot(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater()

    planner_tool_returned = Event()
    planner_continue = Event()
    planner_contents_seen: list[str] = []
    planner_call_count = 0

    def planner_llm(
        contents: list[types.Content],
        config: types.GenerateContentConfig | None,
    ) -> types.GenerateContentResponse:
        nonlocal planner_call_count
        planner_call_count += 1
        serialized = flatten_genai_contents(contents)
        planner_contents_seen.append(serialized)
        if planner_call_count == 1:
            # First call triggers a real tool call so continuation re-enters _build_role_request
            return make_genai_tool_call_response(
                "deep_read_lore",
                {"document": "world.txt"},
                "pln-mid-1",
                candidates_tokens=200,
                thoughts_tokens=100,
            )
        if planner_call_count == 2:
            # Pause before continuation so Turn 2 executes while planner is mid-invocation!
            planner_tool_returned.set()
            assert planner_continue.wait(10.0)
            return make_genai_tool_call_response(
                "update_sticky_note",
                {"topic": "Ambush", "info": "Adapted to Turn 2 in flight."},
                "pln-mid-2",
            )
        if planner_call_count == 3:
            # Complete invocation 1 (if notepad moved again, BackgroundPlanning retries via StaleNoteSnapshot)
            return make_genai_text_response("Finished mid-invocation planning.")
        # Retry pass after StaleNoteSnapshot refreshes snapshot
        if planner_call_count == 4:
            return make_genai_tool_call_response(
                "update_sticky_note",
                {"topic": "Ambush", "info": "Reconciled after stale snapshot retry."},
                "pln-retry-1",
            )
        return make_genai_text_response("Reconciled after retry.")

    rec_calls = 0

    def recorder_llm(
        contents: list[types.Content],
        config: types.GenerateContentConfig | None,
    ) -> types.GenerateContentResponse:
        nonlocal rec_calls
        rec_calls += 1
        return recorder_set_model_response(
            factual_updates={"Inventory": f"Artifact stage {rec_calls}"},
            reason=f"Recorded turn {rec_calls}.",
            call_id=f"rec-mid-{rec_calls}",
        )

    integration_harness.genai_client.planner_handler = planner_llm
    integration_harness.genai_client.recorder_handler = recorder_llm

    bundle = integration_harness.start_engine(theater)

    try:
        # Turn 1 starts planner; wait until planner reaches call #2 (paused after tool execution)
        bundle.engine.process_user_action("TURN_ONE_TRIGGER")
        assert planner_tool_returned.wait(5.0)

        # Turn 2 executes completely while planner is paused mid-invocation!
        # This advances notepad.revision (recorder writes "Artifact stage 2") AND enqueues new planning updates.
        res2 = bundle.engine.resolve_action("TURN_TWO_MID_FLIGHT")
        assert "error" not in res2
        assert bundle.engine.cycle.wait(5.0)
        assert bundle.engine.notepad.snapshot().notes["Inventory"] == "Artifact stage 2"

        # Now release planner! On call #3 (next model continuation), StorySession._build_role_request
        # injects TURN_TWO_MID_FLIGHT via BackgroundPlanning._new_context() and replenishes budget.
        # Because factual Inventory changed since call #1's snapshot, publication either reconciles cleanly
        # or triggers StaleNoteSnapshot retry — either way, final state must preserve BOTH!
        planner_continue.set()
        assert bundle.engine.wait_for_pending_events(10.0)

        # Verify TURN_TWO_MID_FLIGHT was injected into planner requests!
        assert any("TURN_TWO_MID_FLIGHT" in seen for seen in planner_contents_seen[2:])
        final_notes = bundle.engine.notepad.snapshot().notes
        assert final_notes["Inventory"] == "Artifact stage 2"
        assert final_notes["Ambush"] in {
            "Adapted to Turn 2 in flight.",
            "Reconciled after stale snapshot retry.",
        }
    finally:
        planner_continue.set()


def test_planner_token_budget_caps_runaway_tool_loop_via_real_adk_callbacks(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater(
        story_config={
            "deep_planning": {"token_budget_per_event": 500},
        }
    )

    # Gate planner until Turn 1's input, output, and recorded events are ALL queued,
    # so the single planning pass receives one finite 500-token allowance without mid-run refills.
    planner_enabled = False
    planner_calls = 0
    seen_max_output_tokens: list[int | None] = []

    def runaway_planner(
        contents: list[types.Content],
        config: types.GenerateContentConfig | None,
    ) -> types.GenerateContentResponse:
        nonlocal planner_calls
        if not planner_enabled:
            raise make_server_error("Planner gated until foreground completes")
        planner_calls += 1
        seen_max_output_tokens.append(config.max_output_tokens if config is not None else None)
        if planner_calls == 1:
            # Stage a valid planner note while spending 300 tokens (200 candidates + 100 thoughts)
            return make_genai_tool_call_response(
                "update_sticky_note",
                {"topic": "Ambush", "info": "Staged before budget exhaustion."},
                "pln-budget-1",
                candidates_tokens=200,
                thoughts_tokens=100,
            )
        # Keep trying to call tools indefinitely, spending another 300 tokens -> remaining drops <= 0!
        return make_genai_tool_call_response(
            "deep_read_lore",
            {"document": "world.txt"},
            f"pln-budget-{planner_calls}",
            candidates_tokens=200,
            thoughts_tokens=100,
        )

    integration_harness.genai_client.planner_handler = runaway_planner
    bundle = integration_harness.start_engine(theater)

    res = bundle.engine.resolve_action("Survey the upper gallery")
    assert "error" not in res
    assert bundle.engine.cycle.wait(5.0)
    assert not bundle.engine.wait_for_deep_planning(5.0)

    planner_enabled = True
    bundle.engine.retry_pending_turns()
    assert bundle.engine.wait_for_pending_events(10.0)

    # Budget was 500: call 1 used 300 (200 left), call 2 clamped max_output_tokens to 200 and used 300 (-100 left),
    # call 3 was short-circuited by _limit_story_budget without calling genai.Client again!
    assert planner_calls == 2
    assert seen_max_output_tokens == [500, 200]
    # Note staged on call 1 was still committed cleanly!
    assert bundle.engine.notepad.snapshot().notes["Ambush"] == "Staged before budget exhaustion."


def test_engine_restart_recovery_across_mid_flight_recorder_failure_with_real_persistence(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater(
        game_definition=exchange_definition(),
        story_config={"history_event_limit": 10},
    )

    should_act_in_game = False

    def decide(request: DecisionRequest) -> DecisionResult:
        answers: dict[str, DecisionAnswer] = {}
        for q in request.questions:
            if q.name == "should_take_game_action":
                answers[q.name] = PredicateAnswer(
                    name=q.name,
                    probability=0.95 if should_act_in_game else 0.05,
                )
            else:
                answers[q.name] = ChoiceAnswer(name=q.name, choice="none")
        return DecisionResult(provider="fake-decision", model="fake-decision-model", answers=answers)

    integration_harness.decision_provider.handler = decide

    def game_controller(request: TextResponseRequest) -> TextResponseResult:
        decision = GameControlDecision(
            game_actions=(GameAction(name="narratron_place", arguments={"item": "green"}, revision=0),),
            narration_request=None,
            reason="Placed green relic during Turn 2.",
        )
        return TextResponseResult(text=decision.model_dump_json(), provider="fake-text", model=GAME_MODEL)

    integration_harness.text_provider.game_handler = game_controller

    turn_counter = 0

    def responder(request: TextResponseRequest) -> TextResponseResult:
        nonlocal turn_counter
        turn_counter += 1
        return TextResponseResult(
            text=json.dumps({"narration": f"RESTART_NARRATION_{turn_counter}"}),
            provider="fake-text",
            model=RESPONDER_MODEL,
        )

    integration_harness.text_provider.responder_handler = responder

    recorder_fail = False
    rec_step = 0

    def recorder_llm(
        contents: list[types.Content],
        config: types.GenerateContentConfig | None,
    ) -> types.GenerateContentResponse:
        nonlocal rec_step
        if recorder_fail:
            raise make_server_error("503 Recorder crashed mid-flight")
        rec_step += 1
        return recorder_set_model_response(
            factual_updates={"Inventory": f"Recovered item v{rec_step}"},
            reason=f"Recorded v{rec_step}.",
            call_id=f"rec-restart-{rec_step}",
        )

    planner_call_id = 0

    def planner_llm(
        contents: list[types.Content],
        config: types.GenerateContentConfig | None,
    ) -> types.GenerateContentResponse:
        nonlocal planner_call_id
        planner_call_id += 1
        serialized = flatten_genai_contents(contents)
        # Stage PERSISTED_PLANNER_NOTE_V1 if not already persisted or staged in this invocation
        # (handles StaleNoteSnapshot retries cleanly when concurrent recorder advances revision).
        if "PERSISTED_PLANNER_NOTE_V1" not in serialized and "staged." not in serialized:
            return make_genai_tool_call_response(
                "update_sticky_note",
                {"topic": "Ambush", "info": "PERSISTED_PLANNER_NOTE_V1"},
                f"pln-r-{planner_call_id}",
            )
        return make_genai_text_response("Planner acknowledged state.")

    integration_harness.genai_client.recorder_handler = recorder_llm
    integration_harness.genai_client.planner_handler = planner_llm

    # --- Engine Run 1 ---
    first = integration_harness.start_engine(theater)

    # Turn 1 completes fully (including planner sticky note persisted to SQLite)
    res1 = first.engine.resolve_action("TURN_ONE_ACTION", drain=True)
    assert "error" not in res1
    assert first.engine.notepad.snapshot().notes["Inventory"] == "Recovered item v1"
    assert first.engine.notepad.snapshot().notes["Ambush"] == "PERSISTED_PLANNER_NOTE_V1"

    # Turn 2: Responder delivers & StoryGameModule commits narratron_place (game rev -> 1),
    # then ADK recorder crashes with 503!
    should_act_in_game = True
    recorder_fail = True
    res2 = first.engine.resolve_action("TURN_TWO_CRASHED_RECORDER")
    assert "error" in res2
    assert first.canvas.story.narration == "RESTART_NARRATION_2"
    assert first.canvas.game is not None and first.canvas.game.revision == 1
    assert first.engine.turn_journal.turns()[1].game_completed is True
    assert first.engine.turn_journal.turns()[1].recorder_completed is False

    # Shut down Engine 1 completely
    integration_harness.close_bundle(first)

    # --- Engine Run 2 (cold restart from disk) ---
    recorder_fail = False
    should_act_in_game = False
    responder_calls_before_restart = len(integration_harness.text_provider.calls_for("responder"))
    game_calls_before_restart = len(integration_harness.text_provider.calls_for("game"))

    restarted = integration_harness.start_engine(theater)
    assert restarted.engine.wait_for_pending_events(10.0)

    # Turn 2's responder and game controller were NOT re-invoked, and game revision stayed 1!
    assert len(integration_harness.text_provider.calls_for("responder")) == responder_calls_before_restart
    assert len(integration_harness.text_provider.calls_for("game")) == game_calls_before_restart
    assert restarted.canvas.game is not None and restarted.canvas.game.revision == 1

    # Turn 2's recorder caught up cleanly while preserving Turn 1's newer planner sticky!
    notes = restarted.engine.notepad.snapshot().notes
    assert notes["Inventory"] == "Recovered item v2"
    assert notes["Ambush"] == "PERSISTED_PLANNER_NOTE_V1"
    assert all(turn.completed for turn in restarted.engine.turn_journal.turns())

    # Turn 3 after restart sees Turn 1 and Turn 2 in completion history
    res3 = restarted.engine.resolve_action("TURN_THREE_POST_RESTART", drain=True)
    assert "error" not in res3
    turn3_prompt = integration_harness.text_provider.calls_for("responder")[-1].request.prompt
    assert "RESTART_NARRATION_1" in turn3_prompt
    assert "RESTART_NARRATION_2" in turn3_prompt


def test_concurrent_spoken_and_game_ingress_under_variable_backend_latency(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater(
        game_definition=exchange_definition(),
        story_config={"input_cycle_duration": 0.05, "output_cycle_duration": 0.02},
    )

    state_lock = Lock()
    recorder_attempts = 0
    responder_count = 0

    def responder(request: TextResponseRequest) -> TextResponseResult:
        nonlocal responder_count
        time.sleep(0.03)
        with state_lock:
            responder_count += 1
            idx = responder_count
        return TextResponseResult(
            text=json.dumps({"narration": f"CONCURRENT_SCENE_{idx}"}),
            provider="fake-text",
            model=RESPONDER_MODEL,
        )

    def game_controller(request: TextResponseRequest) -> TextResponseResult:
        time.sleep(0.02)
        return TextResponseResult(
            text=GameControlDecision(
                game_actions=(),
                narration_request=None,
                reason="Concurrent game check.",
            ).model_dump_json(),
            provider="fake-text",
            model=GAME_MODEL,
        )

    def recorder_llm(
        contents: list[types.Content],
        config: types.GenerateContentConfig | None,
    ) -> types.GenerateContentResponse:
        nonlocal recorder_attempts
        time.sleep(0.02)
        with state_lock:
            recorder_attempts += 1
            attempt_no = recorder_attempts
        if attempt_no == 1:
            # First recorder call fails transiently; subsequent queued cycle recovers it!
            raise make_server_error("Transient 503 during concurrent burst")
        return recorder_set_model_response(
            factual_updates={"Inventory": f"Concurrent state #{attempt_no}"},
            reason=f"Concurrent commit #{attempt_no}.",
            call_id=f"rec-conc-{attempt_no}",
        )

    integration_harness.text_provider.responder_handler = responder
    integration_harness.text_provider.game_handler = game_controller
    integration_harness.genai_client.recorder_handler = recorder_llm
    integration_harness.genai_client.planner_delay_seconds = 0.03

    bundle = integration_harness.start_engine(theater)
    assert bundle.canvas.game is not None

    def submit_spoken(text: str) -> dict[str, JsonValue]:
        return bundle.engine.process_user_action(text)

    # Fire concurrent spoken actions + real player game moves from multiple threads
    with ThreadPoolExecutor(max_workers=4) as pool:
        f1 = pool.submit(submit_spoken, "Check the northern archway")
        f2 = pool.submit(submit_spoken, "Listen at the iron grate")
        g1 = pool.submit(
            bundle.canvas.game.act,
            GameAction(name="player_place", arguments={"item": "red"}, revision=0),
        )
        assert f1.result(timeout=5.0)["status"] == "processing"
        assert f2.result(timeout=5.0)["status"] == "processing"
        # Game commit returns immediately without waiting for slow LLM calls!
        assert g1.result(timeout=2.0)["revision"] == 1

        # Second game move + another spoken action while earlier batch is running
        g2 = pool.submit(
            bundle.canvas.game.act,
            GameAction(name="player_place", arguments={"item": "blue"}, revision=1),
        )
        f3 = pool.submit(submit_spoken, "Step back from the table")
        assert g2.result(timeout=2.0)["revision"] == 2
        assert f3.result(timeout=5.0)["status"] == "processing"

    # Wait for initial burst, then ensure any transiently failed batch recovers cleanly
    if not bundle.engine.wait_for_pending_events(10.0):
        bundle.engine.retry_pending_turns()
        assert bundle.engine.wait_for_pending_events(10.0)

    turns = bundle.engine.turn_journal.turns()
    assert len(turns) == 5
    assert all(turn.completed for turn in turns)
    assert bundle.engine.recording_journal.pending() == []
    assert bundle.canvas.game.revision == 2
    assert bundle.canvas.game.pending_story_actions() == []
    assert bundle.engine.notepad.snapshot().notes["Inventory"].startswith("Concurrent state #")


def test_game_module_partial_chain_commit_preserves_receipt_across_corrective_retry(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater(game_definition=exchange_definition())

    game_attempt = 0

    def game_controller(request: TextResponseRequest) -> TextResponseResult:
        nonlocal game_attempt
        game_attempt += 1
        if game_attempt == 1:
            # Chain: step 1 is valid (commits rev 1 -> 2), step 2 is invalid (rejected at rev 2!)
            chain = GameControlDecision(
                game_actions=(
                    GameAction(name="narratron_place", arguments={"item": "green"}, revision=1),
                    GameAction(name="narratron_place", arguments={"item": "missing_sapphire"}, revision=2),
                ),
                narration_request=None,
                reason="Attempted 2-step chain where second step is invalid.",
            )
            return TextResponseResult(text=chain.model_dump_json(), provider="fake-text", model=GAME_MODEL)
        # Attempt 2 must see step 1 already committed AND step 2 rejected!
        assert "Already committed Narratron receipts" in request.prompt
        assert '"narratron_place"' in request.prompt
        assert "Rejected Narratron game actions" in request.prompt
        assert "missing_sapphire" in request.prompt
        corrected = GameControlDecision(
            game_actions=(GameAction(name="narratron_finalize", arguments={}, revision=2),),
            narration_request=NarrationRequest(
                user_action="Keeper offers green relic and locks in terms.",
                brief="The Keeper places the green relic and seals the offer.",
            ),
            reason="Completed chain without repeating step 1.",
        )
        return TextResponseResult(text=corrected.model_dump_json(), provider="fake-text", model=GAME_MODEL)

    integration_harness.text_provider.game_handler = game_controller
    bundle = integration_harness.start_engine(theater)
    assert bundle.canvas.game is not None

    bundle.canvas.game.act(GameAction(name="player_place", arguments={"item": "red"}, revision=0))
    assert bundle.engine.wait_for_pending_events(10.0)

    assert game_attempt == 2
    assert bundle.canvas.game.revision == 3
    assert bundle.canvas.game.state["narratron_offer"] == ["green"]
    assert bundle.canvas.game.state["narratron_done"] is True
    turn = bundle.engine.turn_journal.turns()[0]
    assert turn.completed
    receipts = bundle.engine.recording_journal.narratron_receipts(f"{turn.turn_id}:recording")
    assert [r.action.name for r in receipts] == ["narratron_place", "narratron_finalize"]


def test_adk_planner_timeout_leaves_foreground_unblocked_and_recovers_on_retry(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater()

    planner_timeout_started = Event()
    planner_timeout_release = Event()
    fail_with_timeout = True
    planner_calls = 0

    def planner_llm(
        contents: list[types.Content],
        config: types.GenerateContentConfig | None,
    ) -> types.GenerateContentResponse:
        nonlocal planner_calls
        planner_calls += 1
        if fail_with_timeout:
            planner_timeout_started.set()
            planner_timeout_release.wait(5.0)
            return make_genai_text_response("Late timed-out planner output.")
        serialized = flatten_genai_contents(contents)
        if "PLANNER_AFTER_TIMEOUT" not in serialized and "staged." not in serialized:
            return make_genai_tool_call_response(
                "update_sticky_note",
                {"topic": "Ambush", "info": "PLANNER_AFTER_TIMEOUT"},
                f"pln-to-{planner_calls}",
            )
        return make_genai_text_response("Recovered planner.")

    integration_harness.genai_client.planner_handler = planner_llm
    bundle = integration_harness.start_engine(theater)
    bundle.engine.planning_module.deep_planner_timeout_seconds = 0.2

    try:
        res1 = bundle.engine.resolve_action("Check the vault perimeter")
        assert "error" not in res1
        assert planner_timeout_started.wait(5.0)
        # Foreground completes cleanly even while planner hits its timeout!
        assert bundle.engine.cycle.wait(5.0)
        assert not bundle.engine.wait_for_deep_planning(5.0)
        assert bundle.engine.turn_journal.turns()[0].completed

        # Subsequent foreground turn still runs completely unblocked!
        res2 = bundle.engine.resolve_action("Inspect the iron hinges")
        assert "error" not in res2
        assert bundle.engine.cycle.wait(5.0)

        # Recover planner backend and retry pending planning updates
        fail_with_timeout = False
        planner_timeout_release.set()
        bundle.engine.planning_module.deep_planner_timeout_seconds = 10.0
        bundle.engine.retry_pending_turns()
        assert bundle.engine.wait_for_pending_events(10.0)
        assert bundle.engine.notepad.snapshot().notes["Ambush"] == "PLANNER_AFTER_TIMEOUT"
    finally:
        planner_timeout_release.set()


def test_output_presentation_disk_failure_retries_without_rerunning_responder(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater()
    bundle = integration_harness.start_engine(theater)

    original_persist = bundle.canvas.persist
    fail_persist = True

    def flaky_persist() -> None:
        if fail_persist:
            raise OSError("Simulated disk full during scene commit")
        original_persist()

    bundle.canvas.story._persist = flaky_persist

    # Turn 1: Responder succeeds and saves response, then output commit fails on disk persist
    res1 = bundle.engine.resolve_action("Open the sealed chest")
    assert "error" in res1
    assert "disk full" in str(res1["error"])
    assert len(integration_harness.text_provider.calls_for("responder")) == 1
    turn = bundle.engine.turn_journal.turns()[0]
    assert turn.response is not None
    assert not turn.delivered
    assert not turn.recorder_completed

    # Recover disk and retry -> delivers saved response & records without re-calling responder!
    fail_persist = False
    bundle.engine.retry_pending_turns()
    assert bundle.engine.wait_for_pending_events(10.0)
    assert len(integration_harness.text_provider.calls_for("responder")) == 1
    assert bundle.canvas.story.narration == "The brass lock yields with a crisp click."
    assert bundle.engine.turn_journal.turns()[0].delivered
    assert bundle.engine.turn_journal.turns()[0].completed


def test_engine_close_drains_in_flight_work_and_rejects_subsequent_ingress(
    integration_harness: StoryEngineIntegrationHarness,
) -> None:
    theater = integration_harness.prepare_theater(game_definition=exchange_definition())

    responder_started = Event()
    responder_release = Event()

    def slow_responder(request: TextResponseRequest) -> TextResponseResult:
        responder_started.set()
        assert responder_release.wait(5.0)
        return TextResponseResult(
            text=json.dumps({"narration": "Drained cleanly before shutdown."}),
            provider="fake-text",
            model=RESPONDER_MODEL,
        )

    integration_harness.text_provider.responder_handler = slow_responder
    bundle = integration_harness.start_engine(theater)
    assert bundle.canvas.game is not None

    try:
        accepted = bundle.engine.process_user_action("Final action before shutdown")
        assert accepted["status"] == "processing"
        assert responder_started.wait(5.0)

        with ThreadPoolExecutor(max_workers=1) as pool:
            closing = pool.submit(integration_harness.close_bundle, bundle)
            # Release in-flight responder after close() has started draining
            time.sleep(0.05)
            responder_release.set()
            closing.result(timeout=10.0)

        # Post-close spoken ingress raises ValueError and process_user_action returns error
        with pytest.raises(ValueError, match="closed"):
            bundle.engine.submit_action("After shutdown", require_input=False)
        err = bundle.engine.process_user_action("After shutdown", require_input=False)
        assert "error" in err

        # Late player game action callback after close is safely ignored without crashing
        bundle.engine._handle_player_game_action(
            GameAction(name="player_place", arguments={"item": "red"}, revision=0),
            {"revision": 1},
        )
    finally:
        responder_release.set()
