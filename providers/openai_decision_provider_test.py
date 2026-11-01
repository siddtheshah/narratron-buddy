from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from providers.decision_provider import (
    ChoiceQuestion,
    DecisionAttachment,
    DecisionProviderError,
    DecisionRequest,
    DecisionWireAnswer,
    PredicateQuestion,
    ScoreQuestion,
)
from providers.openai_decision_provider import (
    DEFAULT_OPENAI_DECISION_MODEL,
    OpenAIDecisionProvider,
    OpenAIDecisionsWireResponse,
)
from providers.registry import (
    get_decision_provider,
    list_decision_provider_specs,
)


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
        model="gpt-5-luna",
        answers=[
            DecisionWireAnswer(
                type="predicate",
                name="has_readable_text",
                probability=0.92,
            ),
            DecisionWireAnswer(
                type="choice",
                name="animation_technique",
                choice="layered",
                probabilities={"triframe": 0.08, "layered": 0.84, "video": 0.08},
                confidence=0.89,
            ),
            DecisionWireAnswer(
                type="score",
                name="music_fit",
                score=2.65,
                probabilities={"weak": 0.05, "moderate": 0.25, "strong": 0.70},
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
            "choices": ["triframe", "layered", "video"],
        },
        {
            "type": "score",
            "name": "music_fit",
            "instructions": "Rate how well the candidate track matches the mood.",
            "levels": [
                {"value": "weak", "description": "Unrelated genre or instrumentation."},
                {"value": "moderate", "description": "Compatible mood but imperfect pacing."},
                {"value": "strong", "description": "Direct match in mood and instrumentation."},
            ],
        },
    ]

    assert result.provider == "openai-decisions"
    assert result.model == "gpt-5-luna"
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
        model="gpt-5-luna",
        answers=[
            DecisionWireAnswer(
                type="choice",
                name="ref_kind",
                choice="character",
                probabilities={"character": 0.96, "scene": 0.04},
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
        {"type": "input_text", "text": "Sir Galahad draws his silver blade."},
        {"type": "input_image", "image_url": "data:image/png;base64,iVBORw0K"},
    ]


def test_polymorphic_accessor_type_mismatch_and_invalid_wire_answers() -> None:
    wire_response = OpenAIDecisionsWireResponse(
        model="gpt-5-luna",
        answers=[
            DecisionWireAnswer(
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
        model="gpt-5-luna",
        answers=[
            DecisionWireAnswer(
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


def test_registry_decision_provider_resolution() -> None:
    with patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}), patch(
        "providers.openai_decision_provider.OpenAI", return_value=MagicMock()
    ):
        specs = list_decision_provider_specs()
        spec = next(item for item in specs if item["id"] == "openai-decisions")
        assert spec["status"] == "available"
        assert spec["model"] == DEFAULT_OPENAI_DECISION_MODEL

        provider = get_decision_provider("openai-decisions")
        assert provider.id == "openai-decisions"
        assert provider.model == DEFAULT_OPENAI_DECISION_MODEL

        with pytest.raises(DecisionProviderError, match="Unknown decision provider: unknown_id"):
            get_decision_provider("unknown_id")
