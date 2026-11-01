from unittest.mock import MagicMock

from providers.decision_provider import DecisionRequest, PredicateQuestion
from providers.text_response_decision_provider import TextResponseDecisionProvider
from providers.text_response_provider import TextResponseProvider, TextResponseResult


def test_large_predicate_batch_has_room_for_all_json_answers() -> None:
    questions = tuple(PredicateQuestion(name=f"emphasis_{i}", instructions="Emphasize this?") for i in range(40))
    text_provider = MagicMock(spec=TextResponseProvider)
    text_provider.model = "test"
    text_provider.generate.return_value = TextResponseResult(
        provider="test", model="test", text="", parsed={q.name: 0.3 for q in questions},
    )
    result = TextResponseDecisionProvider(text_provider).decide(DecisionRequest(
        input_text="Narration", questions=questions,
    ))
    assert len(result.answers) == 40
    assert text_provider.generate.call_args.args[0].max_output_tokens == 960
