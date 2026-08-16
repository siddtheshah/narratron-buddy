"""Private catalog for reusing generated music without exposing theater assets."""

from __future__ import annotations

import json
import logging
import re
import shutil
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Optional

from providers import TextResponseProvider, TextResponseRequest

logger = logging.getLogger(__name__)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class MusicCatalog:
    """Use TF-IDF to nominate tracks and an LLM to approve the final match."""

    def __init__(
        self,
        directory: Path,
        match_threshold: float = 0.86,
        candidate_count: int = 5,
        reranker_provider: Optional[TextResponseProvider] = None,
        reranker: Optional[Callable[[str, list[dict[str, Any]]], tuple[str, float] | None]] = None,
        database_manager: Any = None,
    ) -> None:
        self.directory = directory
        self.match_threshold = max(0.0, min(1.0, float(match_threshold)))
        self.candidate_count = max(1, int(candidate_count))
        self.reranker_provider = reranker_provider
        self._reranker = reranker
        self.database_manager = database_manager
        self.directory.mkdir(parents=True, exist_ok=True)

    def find_match(self, prompt: str) -> Optional[dict[str, Any]]:
        if not self.database_manager:
            logger.debug("[MusicCatalog] Search skipped: no catalog database is configured.")
            return None
        query_terms = Counter(self._tokens(prompt))
        if not query_terms:
            logger.debug("[MusicCatalog] Search skipped: prompt produced no searchable terms.")
            return None
        try:
            candidates = [entry for entry in self.database_manager.find_music_catalog_candidates(
                query_terms, self.candidate_count
            ) if (self.directory / str(entry.get("filename", ""))).is_file()]
        except Exception as exc:
            # Catalog lookup is an optimization, never a reason to block a
            # requested music generation when Cloud SQL is unavailable.
            logger.warning("[MusicCatalog] Search unavailable: %s", exc)
            return None
        if not candidates:
            logger.debug("[MusicCatalog] Search returned no playable candidates (terms=%d).", len(query_terms))
            return None
        logger.debug("[MusicCatalog] Search selected %d BM25 candidates (terms=%d).", len(candidates), len(query_terms))
        approved = self._rerank(prompt, candidates)
        if not approved:
            logger.debug("[MusicCatalog] Reranker returned no approved candidate.")
            return None
        candidate_id, score = approved
        if score < self.match_threshold:
            logger.debug(
                "[MusicCatalog] Reranker rejected candidate id=%s score=%.2f below threshold=%.2f.",
                candidate_id, score, self.match_threshold,
            )
            return None
        selected = next((entry for entry in candidates if entry["id"] == candidate_id), None)
        if not selected:
            logger.warning("[MusicCatalog] Reranker returned unknown candidate id=%s.", candidate_id)
            return None
        logger.debug("[MusicCatalog] Reranker approved candidate id=%s score=%.2f.", candidate_id, score)
        return {**selected, "score": score, "path": self.directory / selected["filename"]}

    def add(self, source_path: Path, prompt: str, provider: str, model: str) -> dict[str, Any]:
        extension = source_path.suffix.lower() or ".mp3"
        entry = {"id": uuid.uuid4().hex, "filename": f"{uuid.uuid4().hex}{extension}", "prompt": prompt, "provider": provider, "model": model}
        shutil.copy2(source_path, self.directory / entry["filename"])
        if self.database_manager:
            self.database_manager.add_music_catalog_track(
                entry["id"], entry["filename"], prompt, provider, model, Counter(self._tokens(prompt))
            )
            logger.debug("[MusicCatalog] Indexed track id=%s provider=%s model=%s.", entry["id"], provider, model)
        else:
            logger.debug("[MusicCatalog] Saved track id=%s without a searchable database index.", entry["id"])
        return entry

    def _rerank(self, prompt: str, candidates: list[dict[str, Any]]) -> tuple[str, float] | None:
        if self._reranker:
            try:
                return self._reranker(prompt, candidates)
            except Exception as exc:
                logger.warning("[MusicCatalog] Configured reranker failed: %s", exc)
                return None
        if not self.reranker_provider:
            logger.debug("[MusicCatalog] Reranking skipped: no text response provider is configured.")
            return None
        candidate_data = [{"id": entry["id"], "prompt": entry.get("prompt", "")} for entry in candidates]
        instruction = ("Choose the one candidate that would genuinely serve the requested instrumental background music. "
                       "Score semantic and musical fit from 0 to 1; do not select a merely related track. "
                       "Return only JSON: {\"id\": \"candidate id or empty\", \"score\": number}.")
        try:
            response = self.reranker_provider.generate(
                TextResponseRequest(
                    prompt=f"Requested prompt:\n{prompt}\n\nCandidates:\n{json.dumps(candidate_data)}",
                    system_instruction=instruction,
                    temperature=0,
                    max_output_tokens=100,
                )
            )
            data = json.loads(response.text.strip().removeprefix("```json").removesuffix("```").strip())
            return str(data.get("id", "")), max(0.0, min(1.0, float(data.get("score", -1))))
        except Exception as exc:
            logger.warning("[MusicCatalog] Reranker unavailable: %s", exc)
            return None


    @staticmethod
    def _tokens(text: str) -> list[str]:
        return _TOKEN_RE.findall(text.lower())
