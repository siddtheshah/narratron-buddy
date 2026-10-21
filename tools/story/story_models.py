"""Model configuration shared by independent story modules."""

from __future__ import annotations

from collections.abc import AsyncGenerator
import asyncio
from typing import Optional

from google import genai
from google.genai import types
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from pydantic import PrivateAttr


DEFAULT_COMPACTION_TRIGGER_TOKENS = 10_000
DEFAULT_COMPACTION_TARGET_TOKENS = 5_000
DEFAULT_STORY_PLANNING_STYLE = "balanced, consequence-driven, and player-agency-first"


class VertexGemini(Gemini):
    """Gemini model with a client cache isolated per event loop."""

    project_id: Optional[str] = None
    location: Optional[str] = None
    http_options: Optional[types.HttpOptions] = None
    enterprise: bool = False
    malformed_function_call_retries: int = 2
    _client_cache: dict[asyncio.AbstractEventLoop | None, genai.Client] = PrivateAttr(default_factory=dict)

    @property
    def api_client(self) -> genai.Client:
        try:
            loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop not in self._client_cache:
            kwargs: dict[str, str | bool | types.HttpOptions] = {}
            if self.project_id:
                kwargs["project"] = self.project_id
            if self.location:
                kwargs["location"] = self.location
            if self.http_options:
                kwargs["http_options"] = self.http_options
            if kwargs:
                # ``enterprise`` is the current SDK spelling for the Vertex
                # backend. Keep the legacy flag for existing story consumers
                # unless a caller explicitly opts in to Enterprise.
                kwargs["enterprise" if self.enterprise else "vertexai"] = True
            self._client_cache[loop] = genai.Client(**kwargs)
        return self._client_cache[loop]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False,
    ) -> AsyncGenerator[LlmResponse, None]:
        if stream:
            async for response in super().generate_content_async(llm_request, stream=True):
                yield response
            return
        max_attempts = max(1, self.malformed_function_call_retries + 1)
        for attempt in range(max_attempts):
            responses: list[LlmResponse] = []
            async for response in super().generate_content_async(llm_request, stream=False):
                responses.append(response)
            malformed = any(
                resp.error_code == "MALFORMED_FUNCTION_CALL"
                or resp.finish_reason == types.FinishReason.MALFORMED_FUNCTION_CALL
                for resp in responses
            )
            if malformed and attempt + 1 < max_attempts:
                continue
            for response in responses:
                yield response
            return
