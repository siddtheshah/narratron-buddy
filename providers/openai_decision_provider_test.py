from __future__ import annotations

from unittest.mock import MagicMock, patch
import json

import httpx
from openai import OpenAI
import pytest

from providers.decision_provider import (
    ChoiceQuestion,
    DecisionAttachment,
    DecisionProviderError,
    DecisionRequest,
    PredicateQuestion,
    ScoreQuestion,
)
from providers.openai_decision_provider import (
    DEFAULT_OPENAI_DECISION_MODEL,
    OpenAIDecisionProvider,
    OpenAIDecisionWireAnswer,
    OpenAIDecisionProbability,
    OpenAIDecisionsWireResponse,
)
from providers.registry import (
    get_decision_provider,
    list_decision_provider_specs,
)
from providers.text_response_decision_provider import TextResponseDecisionProvider
from providers.text_response_provider import TextResponseProvider, TextResponseResult


def _make_mock_openai_client(response: OpenAIDecisionsWireResponse) -> MagicMock:
    mock_client = MagicMock()
    mock_client.post.return_value = response
    return mock_client


def test_question_and_request_validation() -> None:
    with pytest.raises(ValueError, match="PredicateQuestion name cannot be empty"):
        PredicateQuestion(name=" ", instructions="Is this urgent?")

    with pytest.raises(ValueError, match="ChoiceQuestion 'route' requires at least 2 choices"):
        ChoiceQuestion(name="route", instructions="Pick one", choices=("only_one",))

    with pytest.raises(ValueError, match="ChoiceQuestion 'route' choices must be unique"):
        ChoiceQuestion(name="route", instructions="Pick one", choices=("triframe", "triframe"))

    with pytest.raises(ValueError, match="ScoreQuestion 'fit' requires at least 2 rubric levels"):
        ScoreQuestion(name="fit", instructions="Rate fit", levels=("good",))

    with pytest.raises(ValueError, match="level_descriptions key 'unknown' is not in levels"):
        ScoreQuestion(
            name="fit",
            instructions="Rate fit",
            levels=("low", "high"),
            level_descriptions={"unknown": "not in levels"},
        )

    with pytest.raises(ValueError, match="DecisionRequest input_text cannot be empty"):
        DecisionRequest(
            input_text="   ",
            questions=(PredicateQuestion(name="q1", instructions="check"),),
        )

    with pytest.raises(ValueError, match="DecisionRequest requires at least one question"):
        DecisionRequest(input_text="Some context", questions=())

    with pytest.raises(ValueError, match="Duplicate question name in DecisionRequest: 'q1'"):
        DecisionRequest(
            input_text="Some context",
            questions=(
                PredicateQuestion(name="q1", instructions="first"),
                PredicateQuestion(name="q1", instructions="second"),
            ),
        )


def test_openai_decision_provider_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPEN_API_KEY", raising=False)
    with pytest.raises(
        DecisionProviderError,
        match="OPENAI_API_KEY is not configured for OpenAI Decisions",
    ):
        OpenAIDecisionProvider()


def test_openai_decision_provider_batch_predicate_choice_and_score() -> None:
    wire_response = OpenAIDecisionsWireResponse(
        model=DEFAULT_OPENAI_DECISION_MODEL,
        answers=[
            OpenAIDecisionWireAnswer(
                type="predicate",
                name="has_readable_text",
                probability=0.92,
            ),
            OpenAIDecisionWireAnswer(
                type="choice",
                name="animation_technique",
                choice="layered",
                probabilities=[
                    OpenAIDecisionProbability(value=value, probability=prob)
                    for value, prob in (("triframe", 0.08), ("layered", 0.84), ("video", 0.08))
                ],
                confidence=0.89,
            ),
            OpenAIDecisionWireAnswer(
                type="score",
                name="music_fit",
                score=2.65,
                probabilities=[
                    OpenAIDecisionProbability(value=index, label=label, probability=prob)
                    for index, (label, prob) in enumerate(
                        (("weak", 0.05), ("moderate", 0.25), ("strong", 0.70))
                    )
                ],
                confidence=0.91,
            ),
        ],
        usage={"input_tokens": 128, "output_tokens": 0, "total_tokens": 128},
    )
    wire_response._request_id = "dec_req_001"
    mock_client = _make_mock_openai_client(wire_response)

    provider = OpenAIDecisionProvider(client=mock_client)
    request = DecisionRequest(
        input_text="A lantern sways gently above a mossy stone archway with carved runes.",
        questions=(
            PredicateQuestion(
                name="has_readable_text",
                instructions="Does the prompt require rendering legible words or lettering?",
            ),
            ChoiceQuestion(
                name="animation_technique",
                instructions="Select the best animation technique for the scene.",
                choices=("triframe", "layered", "video"),
            ),
            ScoreQuestion(
                name="music_fit",
                instructions="Rate how well the candidate track matches the mood.",
                levels=("weak", "moderate", "strong"),
                level_descriptions={
                    "weak": "Unrelated genre or instrumentation.",
                    "moderate": "Compatible mood but imperfect pacing.",
                    "strong": "Direct match in mood and instrumentation.",
                },
            ),
        ),
    )

    result = provider.decide(request)

    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert call_args.args[0] == "/decisions"
    assert call_args.kwargs["cast_to"] is OpenAIDecisionsWireResponse
    sent_body = call_args.kwargs["body"]
    assert sent_body["model"] == DEFAULT_OPENAI_DECISION_MODEL
    assert sent_body["input"] == "A lantern sways gently above a mossy stone archway with carved runes."
    assert sent_body["questions"] == [
        {
            "type": "predicate",
            "name": "has_readable_text",
            "instructions": "Does the prompt require rendering legible words or lettering?",
        },
        {
            "type": "choice",
            "name": "animation_technique",
            "instructions": "Select the best animation technique for the scene.",
            "choices": [{"value": "triframe"}, {"value": "layered"}, {"value": "video"}],
        },
        {
            "type": "score",
            "name": "music_fit",
            "instructions": "Rate how well the candidate track matches the mood.",
            "levels": [
                {"label": "weak", "description": "Unrelated genre or instrumentation."},
                {"label": "moderate", "description": "Compatible mood but imperfect pacing."},
                {"label": "strong", "description": "Direct match in mood and instrumentation."},
            ],
        },
    ]

    assert result.provider == "openai-decisions"
    assert result.model == DEFAULT_OPENAI_DECISION_MODEL
    assert result.request_id == "dec_req_001"
    assert result.usage["input_tokens"] == 128

    pred = result.get_predicate("has_readable_text")
    assert pred.probability == pytest.approx(0.92)
    assert pred.passed(0.8) is True
    assert pred.passed(0.95) is False

    choice = result.get_choice("animation_technique")
    assert choice.choice == "layered"
    assert choice.confidence == pytest.approx(0.89)
    assert choice.probabilities["layered"] == pytest.approx(0.84)

    score = result.get_score("music_fit")
    assert score.score == pytest.approx(2.65)
    assert score.confidence == pytest.approx(0.91)
    assert score.probabilities["strong"] == pytest.approx(0.70)


def test_single_question_convenience_helpers_and_multimodal_attachments() -> None:
    wire_response = OpenAIDecisionsWireResponse(
        model=DEFAULT_OPENAI_DECISION_MODEL,
        answers=[
            OpenAIDecisionWireAnswer(
                type="choice",
                name="ref_kind",
                choice="character",
                probabilities=[
                    OpenAIDecisionProbability(value=value, probability=prob)
                    for value, prob in (("character", 0.96), ("scene", 0.04))
                ],
                confidence=0.96,
            )
        ],
    )
    mock_client = _make_mock_openai_client(wire_response)
    provider = OpenAIDecisionProvider(client=mock_client)

    answer = provider.choose(
        "Sir Galahad draws his silver blade.",
        "Classify <SirGalahad> as a character or a scene.",
        ("character", "scene"),
        name="ref_kind",
        attachments=(DecisionAttachment(data=b"\x89PNG\r\n", mime_type="image/png"),),
    )

    assert answer.choice == "character"
    sent_body = mock_client.post.call_args.kwargs["body"]
    assert sent_body["input"] == [
        {"role": "user", "content": [
            {"type": "input_text", "text": "Sir Galahad draws his silver blade."},
            {"type": "input_image", "image_url": "data:image/png;base64,iVBORw0K"},
        ]},
    ]


def test_polymorphic_accessor_type_mismatch_and_invalid_wire_answers() -> None:
    wire_response = OpenAIDecisionsWireResponse(
        model=DEFAULT_OPENAI_DECISION_MODEL,
        answers=[
            OpenAIDecisionWireAnswer(
                type="predicate",
                name="is_combat",
                probability=0.75,
            )
        ],
    )
    mock_client = _make_mock_openai_client(wire_response)
    provider = OpenAIDecisionProvider(client=mock_client)

    result = provider.decide(
        DecisionRequest(
            input_text="Blades clash in the courtyard.",
            questions=(PredicateQuestion(name="is_combat", instructions="Is combat happening?"),),
        )
    )

    with pytest.raises(
        DecisionProviderError,
        match="Question 'is_combat' returned a 'predicate' answer, expected 'choice'",
    ):
        result.get_choice("is_combat")

    # Reject out-of-vocabulary choice returned by wire API
    bad_choice_response = OpenAIDecisionsWireResponse(
        model=DEFAULT_OPENAI_DECISION_MODEL,
        answers=[
            OpenAIDecisionWireAnswer(
                type="choice",
                name="technique",
                choice="hologram",
            )
        ],
    )
    bad_provider = OpenAIDecisionProvider(client=_make_mock_openai_client(bad_choice_response))
    with pytest.raises(
        DecisionProviderError,
        match="Choice answer 'hologram' for question 'technique' is not in allowed choices",
    ):
        bad_provider.choose("A campfire flickers.", "Pick technique", ("triframe", "layered"), name="technique")


def test_text_response_decision_provider_baseline_evaluates_batch() -> None:
    mock_text_provider = MagicMock(spec=TextResponseProvider)
    mock_text_provider.model = "gemini-3.1-flash-lite-preview"
    mock_text_provider.generate.return_value = TextResponseResult(
        provider="gemini-2-5",
        model="gemini-3.1-flash-lite-preview",
        text='{"has_readable_text": true, "scene_type": "complex", "music_fit": "strong"}',
        parsed={
            "has_readable_text": True,
            "scene_type": "complex",
            "music_fit": "strong",
        },
        request_id="gem_req_1",
        usage={"prompt_token_count": 95, "candidates_token_count": 22},
    )

    decision_provider = TextResponseDecisionProvider(
        mock_text_provider,
        provider_id="gemini-2-5-decisions",
        display_name="Gemini Flash-Lite JSON (Baseline)",
    )
    request = DecisionRequest(
        input_text="An ornate tavern signboard reading 'THE GILDEDWYRM' above a crowd.",
        questions=(
            PredicateQuestion(name="has_readable_text", instructions="Does it contain text?"),
            ChoiceQuestion(
                name="scene_type",
                instructions="Classify scene type.",
                choices=("character", "scene", "complex"),
            ),
            ScoreQuestion(
                name="music_fit",
                instructions="Rate musical fit.",
                levels=("weak", "moderate", "strong"),
            ),
        ),
    )

    result = decision_provider.decide(request)

    mock_text_provider.generate.assert_called_once()
    sent_text_req = mock_text_provider.generate.call_args.args[0]
    assert sent_text_req.temperature == 0.0
    assert sent_text_req.response_json_schema is not None
    assert sent_text_req.response_json_schema["required"] == [
        "has_readable_text",
        "scene_type",
        "music_fit",
    ]

    assert result.provider == "gemini-2-5-decisions"
    assert result.model == "gemini-3.1-flash-lite-preview"
    assert result.get_predicate("has_readable_text").passed() is True
    assert result.get_choice("scene_type").choice == "complex"
    assert result.get_score("music_fit").score == pytest.approx(2.0)


def test_registry_decision_provider_resolution() -> None:
    with (
        patch.dict("os.environ", {"OPENAI_API_KEY": "test-key", "GEMINI_API_KEY": "test-gem-key"}),
        patch("providers.openai_decision_provider.OpenAI", return_value=MagicMock()),
        patch("providers.gemini_text_response_provider.genai.Client", return_value=MagicMock()),
    ):
        specs = list_decision_provider_specs()
        spec_ids = {item["id"] for item in specs}
        assert {"openai-decisions", "gemini-2-5-decisions", "gemini-3-decisions"}.issubset(spec_ids)

        openai_spec = next(item for item in specs if item["id"] == "openai-decisions")
        assert openai_spec["status"] == "available"
        assert openai_spec["model"] == DEFAULT_OPENAI_DECISION_MODEL

        provider = get_decision_provider("openai-decisions")
        assert provider.id == "openai-decisions"
        assert provider.model == DEFAULT_OPENAI_DECISION_MODEL

        baseline = get_decision_provider("gemini-2-5-decisions")
        assert baseline.id == "gemini-2-5-decisions"

        with pytest.raises(DecisionProviderError, match="Unknown decision provider: unknown_id"):
            get_decision_provider("unknown_id")


def test_openai_decisions_parses_real_http_response() -> None:
    """Exercise SDK decoding using the documented API request and response shapes."""
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/decisions"
        body = json.loads(request.content)
        assert body["model"] == "gpt-6-luna"
        assert body["questions"][1]["choices"] == [{"value": "forest"}, {"value": "city"}]
        assert body["questions"][2]["levels"] == [{"label": "low"}, {"label": "high"}]
        return httpx.Response(
            200,
            headers={"x-request-id": "req_decisions_http"},
            json={
                "model": "gpt-6-luna",
                "answers": [
                    {"type": "predicate", "name": "quiet", "probability": 0.95},
                    {
                        "type": "choice", "name": "scene", "choice": "forest",
                        "probabilities": [
                            {"value": "forest", "probability": 0.9},
                            {"value": "city", "probability": 0.1},
                        ],
                        "confidence": 0.8,
                    },
                    {
                        "type": "score", "name": "noise", "score": 0.1,
                        "probabilities": [
                            {"value": 0, "label": "low", "probability": 0.9},
                            {"value": 1, "label": "high", "probability": 0.1},
                        ],
                        "confidence": 0.8,
                    },
                ],
                "usage": {"input_tokens": 364, "output_tokens": 0, "total_tokens": 364},
            },
        )

    with OpenAI(
        api_key="test-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as client:
        result = OpenAIDecisionProvider(client=client).decide(DecisionRequest(
            input_text="A quiet forest clearing.",
            questions=(
                PredicateQuestion(name="quiet", instructions="Is the scene quiet?"),
                ChoiceQuestion(name="scene", instructions="Classify the scene.", choices=("forest", "city")),
                ScoreQuestion(name="noise", instructions="Rate the noise.", levels=("low", "high")),
            ),
        ))

    assert result.request_id == "req_decisions_http"
    assert result.get_predicate("quiet").probability == pytest.approx(0.95)
    assert result.get_choice("scene").probabilities == {"forest": 0.9, "city": 0.1}
    assert result.get_score("noise").probabilities == {"low": 0.9, "high": 0.1}
    assert result.get_score("noise").score == pytest.approx(0.1)
    assert result.usage["input_tokens"] == 364


def test_openai_decisions_refusal_reports_question_name() -> None:
    response = OpenAIDecisionsWireResponse(answers=[
        OpenAIDecisionWireAnswer(type="refusal", name="quiet"),
    ])
    provider = OpenAIDecisionProvider(client=_make_mock_openai_client(response))
    with pytest.raises(DecisionProviderError, match="refused to answer decision question 'quiet'"):
        provider.decide(DecisionRequest(
            input_text="A forest.",
            questions=(PredicateQuestion(name="quiet", instructions="Is it quiet?"),),
        ))


def test_score_question_rejects_more_than_ten_levels() -> None:
    with pytest.raises(ValueError, match="supports at most 10 rubric levels"):
        ScoreQuestion(name="noise", instructions="Rate noise", levels=tuple(str(i) for i in range(11)))
