"""Model configuration shared by independent story modules."""

from __future__ import annotations

import asyncio
from typing import Optional

from google import genai
from google.adk.models.google_llm import Gemini
from pydantic import PrivateAttr


DEFAULT_COMPACTION_TRIGGER_TOKENS = 12_000
DEFAULT_COMPACTION_TARGET_TOKENS = 6_000
DEFAULT_STORY_PLANNING_STYLE = "balanced, consequence-driven, and player-agency-first"


class VertexGemini(Gemini):
    """Gemini model with a client cache isolated per event loop."""

    project_id: Optional[str] = None
    location: Optional[str] = None
    _client_cache: dict = PrivateAttr(default_factory=dict)

    @property
    def api_client(self):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop not in self._client_cache:
            kwargs = {}
            if self.project_id:
                kwargs["project"] = self.project_id
            if self.location:
                kwargs["location"] = self.location
            if kwargs:
                kwargs["vertexai"] = True
            self._client_cache[loop] = genai.Client(**kwargs)
        return self._client_cache[loop]
