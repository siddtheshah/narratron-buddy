"""Gemini implementation of the shared text-response-provider contract."""

from __future__ import annotations

import os
from copy import deepcopy
from typing import Any

from google import genai
from google.genai import types

from providers.text_response_provider import (
    TextResponseProvider,
    TextResponseProviderError,
    TextResponseRequest,
    TextResponseResult,
)


class GeminiTextResponseProvider(TextResponseProvider):
    id = "gemini-2-5"
    display_name = "Gemini 2.5 Flash-Lite Text (Vertex AI)"

    def __init__(
        self,
        model: str = "gemini-2.5-flash-lite",
        client: Any = None,
        project: str | None = None,
        location: str | None = None,
    ):
        self.model = model
        if client is None:
            project_id = (
                project
                or os.getenv("GOOGLE_CLOUD_PROJECT")
                or os.getenv("GCP_PROJECT")
                or os.getenv("GOOGLE_PROJECT_ID")
            )
            loc = (
                location
                or os.getenv("GOOGLE_CLOUD_LOCATION")
                or os.getenv("VERTEX_LOCATION")
                or "us-central1"
            )
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            has_creds = bool(os.getenv("GOOGLE_APPLICATION_CREDENTIALS"))

            if not project_id and not api_key and not has_creds:
                raise TextResponseProviderError(
                    "GOOGLE_CLOUD_PROJECT or Vertex AI credentials are not configured for Gemini Text Response."
                )

            if project_id:
                client_kwargs: dict[str, Any] = {"vertexai": True, "project": project_id}
                if loc:
                    client_kwargs["location"] = loc
            elif api_key:
                client_kwargs = {"api_key": api_key}
            else:
                client_kwargs = {"vertexai": True}
                if loc:
                    client_kwargs["location"] = loc

            try:
                client = genai.Client(**client_kwargs)
            except Exception as exc:
                raise TextResponseProviderError(f"Failed to initialize Vertex AI client: {exc}") from exc
        self.client = client



    def generate(self, request: TextResponseRequest) -> TextResponseResult:
        config_kwargs: dict[str, Any] = {}
        if request.system_instruction:
            config_kwargs["system_instruction"] = request.system_instruction
        if request.temperature is not None:
            config_kwargs["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            config_kwargs["max_output_tokens"] = request.max_output_tokens
        if request.stop_sequences:
            config_kwargs["stop_sequences"] = list(request.stop_sequences)

        schema = request.response_json_schema or request.response_schema
        if schema is not None:
            config_kwargs["response_mime_type"] = "application/json"
            if request.response_json_schema is not None:
                config_kwargs["response_json_schema"] = deepcopy(request.response_json_schema)
            else:
                # google-genai normalizes a dict schema in place before sending it.
                # Keep the request's original JSON Schema for local validation.
                config_kwargs["response_schema"] = deepcopy(request.response_schema)


        config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None
        contents: Any = request.prompt
        if request.attachments:
            contents = [request.prompt]
            contents.extend(
                types.Part.from_bytes(data=attachment.data, mime_type=attachment.mime_type)
                for attachment in request.attachments
            )

        try:
            kwargs: dict[str, Any] = {
                "model": request.model or self.model,
                "contents": contents,
            }
            if config:
                kwargs["config"] = config
            response = self.client.models.generate_content(**kwargs)
        except Exception as exc:
            raise TextResponseProviderError(f"Gemini text request failed: {exc}") from exc

        text = self._extract_text(response)

        if not text:
            raise TextResponseProviderError(self._failure_message(response))

        parsed = None
        if schema is not None:
            parsed = self.validate_structured_response(schema, text)


        finish_reason = self._extract_finish_reason(response)
        usage = self._extract_usage(response)
        request_id = getattr(response, "response_id", None) or getattr(response, "request_id", None)

        return TextResponseResult(
            text=text,
            provider=self.id,
            model=request.model or self.model,
            request_id=request_id,
            finish_reason=finish_reason,
            usage=usage,
            parsed=parsed,
        )


    @classmethod
    def _extract_text(cls, response: Any) -> str | None:
        if response is None:
            return None

        # Handle streaming responses or iterables of chunks
        if hasattr(response, "__iter__") and not isinstance(response, (dict, str, bytes, tuple, list)):
            chunks: list[str] = []
            try:
                for chunk in response:
                    chunk_text = cls._extract_single_response_text(chunk)
                    if chunk_text:
                        chunks.append(chunk_text)
                if chunks:
                    return "".join(chunks)
            except Exception:
                pass

        return cls._extract_single_response_text(response)

    @classmethod
    def _extract_single_response_text(cls, response: Any) -> str | None:
        # Check candidates first to ensure we collect all parts across candidates
        candidates = getattr(response, "candidates", None) or []
        if isinstance(response, dict):
            candidates = response.get("candidates", [])

        collected_texts: list[str] = []
        for candidate in candidates:
            content = candidate.get("content") if isinstance(candidate, dict) else getattr(candidate, "content", None)
            parts = content.get("parts") if isinstance(content, dict) else getattr(content, "parts", None)
            for part in parts or []:
                part_text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
                if part_text:
                    collected_texts.append(part_text)

        if collected_texts:
            return "".join(collected_texts)

        # Fallback to response.text if candidates list was empty or unstructured
        try:
            text = getattr(response, "text", None)
            if isinstance(text, str) and text.strip():
                return text
        except Exception:
            pass

        return None


    @staticmethod
    def _extract_finish_reason(response: Any) -> str | None:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            candidate = candidates[0]
            reason = candidate.get("finish_reason") if isinstance(candidate, dict) else getattr(candidate, "finish_reason", None)
            if reason:
                return str(reason)
        return None

    @staticmethod
    def _failure_message(response: Any) -> str:
        candidates = getattr(response, "candidates", None) or []
        if candidates:
            candidate = candidates[0]
            reason = candidate.get("finish_reason") if isinstance(candidate, dict) else getattr(candidate, "finish_reason", None)
            if reason:
                return f"Gemini returned no text response (finish reason: {reason})."
        return "Gemini returned no text response content."

    @staticmethod
    def _extract_usage(response: Any) -> dict[str, Any]:
        metadata = getattr(response, "usage_metadata", None)
        if metadata is None:
            return {}
        if hasattr(metadata, "model_dump"):
            return metadata.model_dump(exclude_none=True)
        if isinstance(metadata, dict):
            return dict(metadata)
        return {key: value for key, value in vars(metadata).items() if not key.startswith("_")}
