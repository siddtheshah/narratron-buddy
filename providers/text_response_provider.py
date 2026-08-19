"""Small, provider-neutral contract for text generation and benchmarking."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from pydantic import BaseModel, TypeAdapter


class TextResponseProviderError(RuntimeError):
    """A provider-normalized text response failure safe to show in diagnostics."""


def parse_and_validate_structured_response(schema: Any, text: str) -> Any:
    """Parse JSON text and validate against the supplied structured schema."""
    if schema is None:
        return None

    clean_text = text.strip() if text else ""
    if clean_text.startswith("```"):
        lines = clean_text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        clean_text = "\n".join(lines).strip()

    if not clean_text:
        raise TextResponseProviderError("Response text is empty; cannot validate against structured schema.")

    try:
        data = json.loads(clean_text)
    except Exception as exc:
        raise TextResponseProviderError(f"Response is not valid JSON for structured schema: {exc}") from exc

    if isinstance(schema, type) and issubclass(schema, BaseModel):
        try:
            return schema.model_validate(data)
        except Exception as exc:
            raise TextResponseProviderError(f"Response failed schema validation for {schema.__name__}: {exc}") from exc

    if isinstance(schema, type):
        try:
            adapter = TypeAdapter(schema)
            return adapter.validate_python(data)
        except Exception as exc:
            raise TextResponseProviderError(f"Response failed schema validation for {schema}: {exc}") from exc

    if isinstance(schema, dict):
        try:
            import jsonschema

            jsonschema.validate(instance=data, schema=schema)
            return data
        except ImportError:
            required = schema.get("required", [])
            if isinstance(data, dict) and isinstance(required, (list, tuple)):
                for req_key in required:
                    if req_key not in data:
                        raise TextResponseProviderError(f"Response missing required schema field: '{req_key}'")
            return data
        except Exception as exc:
            raise TextResponseProviderError(f"Response failed JSON schema validation: {exc}") from exc

    if callable(schema):
        try:
            return schema(data)
        except Exception as exc:
            raise TextResponseProviderError(f"Response failed custom schema validation: {exc}") from exc

    return data


@dataclass(frozen=True)
class TextResponseRequest:
    prompt: str
    system_instruction: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    stop_sequences: Sequence[str] = ()
    response_schema: type[BaseModel] | Any | None = None

    def __post_init__(self) -> None:
        if not self.prompt or not self.prompt.strip():
            raise ValueError("Text response prompt cannot be empty.")
        if self.temperature is not None and (self.temperature < 0.0 or self.temperature > 2.0):
            raise ValueError("Temperature must be between 0.0 and 2.0.")
        if self.max_output_tokens is not None and self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive.")



@dataclass(frozen=True)
class TextResponseResult:
    text: str
    provider: str
    model: str
    request_id: str | None = None
    finish_reason: str | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    parsed: Any | None = None


class TextResponseProvider(ABC):
    """An adapter that turns one request into a generated text response."""

    id: str
    display_name: str
    model: str

    @abstractmethod
    def generate(self, request: TextResponseRequest) -> TextResponseResult:
        """Generate text or raise :class:`TextResponseProviderError`."""

    def validate_structured_response(self, schema: Any, text: str) -> Any:
        """Helper to parse and validate text against a structured schema."""
        return parse_and_validate_structured_response(schema, text)
