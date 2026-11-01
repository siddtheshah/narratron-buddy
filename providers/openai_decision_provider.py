"""OpenAI Decisions API (/v1/decisions) implementation of DecisionProvider."""

from __future__ import annotations

import base64
import os

from openai import BaseModel as OpenAIBaseModel, OpenAI
from pydantic import Field, JsonValue

from providers.decision_provider import (
    DecisionAnswer,
    DecisionProvider,
    DecisionProviderError,
    DecisionRequest,
    DecisionResult,
    DecisionWireAnswer,
)

DEFAULT_OPENAI_DECISION_MODEL = "gpt-5-luna"


class OpenAIDecisionsWireResponse(OpenAIBaseModel):
    """Wire schema returned by POST /v1/decisions."""

    model: str | None = None
    answers: list[DecisionWireAnswer] = Field(default_factory=list)
    usage: dict[str, JsonValue] | None = None
    _request_id: str | None = None


class OpenAIDecisionProvider(DecisionProvider):
    """Evaluates predicate, choice, and score questions via OpenAI's /v1/decisions endpoint."""

    id = "openai-decisions"
    display_name = "OpenAI Decisions (gpt-5-luna)"

    def __init__(
        self,
        model: str = DEFAULT_OPENAI_DECISION_MODEL,
        client: OpenAI | None = None,
    ) -> None:
        if not model or not model.strip():
            raise DecisionProviderError("OpenAI decision model cannot be empty.")
        self.model = model.strip()
        if client is None:
            api_key = os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_API_KEY")
            if not api_key:
                raise DecisionProviderError(
                    "OPENAI_API_KEY is not configured for OpenAI Decisions."
                )
            client = OpenAI(api_key=api_key)
        self.client = client

    def decide(self, request: DecisionRequest) -> DecisionResult:
        target_model = (request.model or self.model).strip()
        if not target_model:
            raise DecisionProviderError("OpenAI decision model cannot be empty.")

        body: dict[str, JsonValue] = {
            "model": target_model,
            "input": self._build_input_payload(request),
            "questions": [question.to_wire_dict() for question in request.questions],
        }

        try:
            response = self.client.post(
                "/decisions",
                cast_to=OpenAIDecisionsWireResponse,
                body=body,
            )
        except DecisionProviderError:
            raise
        except Exception as exc:
            raise DecisionProviderError(f"OpenAI decisions request failed: {exc}") from exc

        if not response.answers:
            raise DecisionProviderError("OpenAI returned no decision answers.")

        wire_by_name: dict[str, DecisionWireAnswer] = {
            answer.name: answer for answer in response.answers
        }

        parsed_answers: dict[str, DecisionAnswer] = {}
        for question in request.questions:
            if question.name not in wire_by_name:
                raise DecisionProviderError(
                    f"OpenAI decisions response omitted answer for question '{question.name}'."
                )
            parsed_answers[question.name] = question.parse_wire_answer(
                wire_by_name[question.name]
            )

        usage: dict[str, JsonValue] = (
            dict(response.usage) if response.usage is not None else {}
        )
        return DecisionResult(
            provider=self.id,
            model=response.model or target_model,
            answers=parsed_answers,
            request_id=response._request_id,
            usage=usage,
        )

    @staticmethod
    def _build_input_payload(request: DecisionRequest) -> JsonValue:
        if not request.attachments:
            return request.input_text
        parts: list[JsonValue] = [
            {
                "type": "input_text",
                "text": request.input_text,
            }
        ]
        for attachment in request.attachments:
            encoded = base64.b64encode(attachment.data).decode("ascii")
            parts.append(
                {
                    "type": "input_image",
                    "image_url": f"data:{attachment.mime_type};base64,{encoded}",
                }
            )
        return parts
