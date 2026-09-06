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

from absl import flags

from providers import (
    TextResponseProvider,
    TextResponseRequest,
    get_text_response_provider,
)
from storage.database import DatabaseManager

logger = logging.getLogger(__name__)
_TOKEN_RE = re.compile(r"[a-z0-9]+")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

if "testing_use_local" not in flags.FLAGS:
    flags.DEFINE_boolean(
        "testing_use_local",
        False,
        "Use local resources (database, adventures, theater repository) for testing and development.",
    )

FLAGS = flags.FLAGS


def get_music_catalog_root() -> Path:
    """Return the music catalog root for the selected runtime environment."""
    if "testing_use_local" in FLAGS and FLAGS["testing_use_local"].value:
        return PROJECT_ROOT / "music_catalog"
    return Path("/mnt/storage/music_catalog")


def ensure_music_catalog_root() -> Path:
    """Return and create the selected music catalog root."""
    catalog_root = get_music_catalog_root().resolve()
    catalog_root.mkdir(parents=True, exist_ok=True)
    return catalog_root


get_music_catalog_dir = get_music_catalog_root
ensure_music_catalog_dir = ensure_music_catalog_root


class MusicCatalog:
    """Use TF-IDF to nominate tracks and an LLM to approve the final match."""

    def __init__(
        self,
        directory: Optional[Path] = None,
        database_manager: Optional[DatabaseManager] = None,
        match_threshold: float = 0.86,
        candidate_count: int = 5,
        reranker_provider: Optional[TextResponseProvider] = None,
        reranker: Optional[Callable[[str, list[dict[str, Any]]], tuple[str, float] | None]] = None,
    ) -> None:
        if database_manager is None:
            raise ValueError("database_manager is required")

        self.directory = Path(directory) if directory is not None else get_music_catalog_root()
        self.database_manager = database_manager
        self.match_threshold = max(0.0, min(1.0, float(match_threshold)))
        self.candidate_count = max(1, int(candidate_count))
        self.reranker_provider = reranker_provider
        self._reranker = reranker
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("[MusicCatalog] Could not create directory: %s", self.directory)

    @classmethod
    def from_config(
        cls,
        config: Optional[dict] = None,
        database_manager: Optional[DatabaseManager] = None,
        directory: Optional[Path] = None,
    ) -> MusicCatalog:
        if database_manager is None:
            try:
                import object_registry
                database_manager = getattr(object_registry, "db", None)
            except Exception:
                database_manager = None
        if database_manager is None:
            raise ValueError("database_manager is required")

        music_config = (config or {}).get("music", {}) if isinstance(config, dict) else {}
        reranker_provider = None
        try:
            reranker_provider = get_text_response_provider(
                str(music_config.get("catalog_reranker_provider", "gemini-2-5")),
                {"model": str(music_config.get("catalog_reranker_model", "gemini-2.5-flash-lite"))},
            )
        except Exception as exc:
            logger.warning("[MusicCatalog] Could not initialize reranker provider: %s", exc)

        return cls(
            directory=directory or get_music_catalog_root(),
            database_manager=database_manager,
            match_threshold=float(music_config.get("catalog_match_threshold", 0.86)),
            candidate_count=int(music_config.get("catalog_candidate_count", 5)),
            reranker_provider=reranker_provider,
        )

    def find_match(self, prompt: str) -> Optional[dict[str, Any]]:
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
        self.directory.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, self.directory / entry["filename"])
        self.database_manager.add_music_catalog_track(
            entry["id"], entry["filename"], prompt, provider, model, Counter(self._tokens(prompt))
        )
        logger.debug("[MusicCatalog] Indexed track id=%s provider=%s model=%s.", entry["id"], provider, model)
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
