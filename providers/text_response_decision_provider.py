"""Adapter that evaluates DecisionRequest questions via a structured-JSON TextResponseProvider."""

from __future__ import annotations

from pydantic import JsonValue, TypeAdapter, ValidationError

from providers.decision_provider import (
    DecisionAnswer,
    DecisionProvider,
    DecisionProviderError,
    DecisionRequest,
    DecisionResult,
)
from providers.text_response_provider import (
    TextResponseAttachment,
    TextResponseProvider,
    TextResponseProviderError,
    TextResponseRequest,
)

_SYSTEM_INSTRUCTION = (
    "You are a fast, deterministic decision classifier. Evaluate the input evidence "
    "and answer every requested field strictly according to the JSON schema."
)


class TextResponseDecisionProvider(DecisionProvider):
    """Evaluates DecisionRequest batches using a generative TextResponseProvider with JSON schema."""

    def __init__(
        self,
        text_provider: TextResponseProvider,
        *,
        provider_id: str = "gemini-2-5-decisions",
        display_name: str = "Gemini Flash-Lite JSON Decisions (Baseline)",
        model: str | None = None,
    ) -> None:
        self.text_provider = text_provider
        self.id = provider_id
        self.display_name = display_name
        self.model = model or text_provider.model

    def decide(self, request: DecisionRequest) -> DecisionResult:
        target_model = (request.model or self.model).strip()
        properties: dict[str, JsonValue] = {
            question.name: question.to_json_schema_property()
            for question in request.questions
        }
        required: list[JsonValue] = [question.name for question in request.questions]
        json_schema: dict[str, JsonValue] = {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }

        attachments = tuple(
            TextResponseAttachment(data=att.data, mime_type=att.mime_type)
            for att in request.attachments
        )

        text_req = TextResponseRequest(
            prompt=request.input_text,
            model=target_model,
            system_instruction=_SYSTEM_INSTRUCTION,
            temperature=0.0,
            max_output_tokens=max(256, 24 * len(request.questions)),
            response_json_schema=json_schema,
            attachments=attachments,
        )

        try:
            text_result = self.text_provider.generate(text_req)
        except TextResponseProviderError as exc:
            raise DecisionProviderError(str(exc)) from exc
        except Exception as exc:
            raise DecisionProviderError(f"Text-backed decision request failed: {exc}") from exc

        try:
            parsed_map = TypeAdapter(dict[str, JsonValue]).validate_python(text_result.parsed)
        except ValidationError as exc:
            raise DecisionProviderError(
                f"Text-backed decision response did not return a JSON object: {exc}"
            ) from exc

        answers: dict[str, DecisionAnswer] = {}
        for question in request.questions:
            if question.name not in parsed_map:
                raise DecisionProviderError(
                    f"Text-backed decision response omitted field '{question.name}'."
                )
            answers[question.name] = question.parse_structured_json_value(
                parsed_map[question.name]
            )

        usage = TypeAdapter(dict[str, JsonValue]).validate_python(dict(text_result.usage))
        return DecisionResult(
            provider=self.id,
            model=text_result.model or target_model,
            answers=answers,
            request_id=text_result.request_id,
            usage=usage,
        )
