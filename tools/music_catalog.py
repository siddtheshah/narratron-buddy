"""Private catalog for reusing generated music without exposing theater assets."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import threading
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class MusicCatalog:
    """Use TF-IDF to nominate tracks and an LLM to approve the final match."""

    def __init__(
        self,
        directory: Path,
        match_threshold: float = 0.86,
        candidate_count: int = 5,
        reranker_model: str = "gemini-2.5-flash-lite",
        reranker: Optional[Callable[[str, list[dict[str, Any]]], tuple[str, float] | None]] = None,
    ) -> None:
        self.directory = directory
        self.metadata_path = directory / "catalog.json"
        self.match_threshold = max(0.0, min(1.0, float(match_threshold)))
        self.candidate_count = max(1, int(candidate_count))
        self.reranker_model = reranker_model
        self._reranker = reranker
        self._lock = threading.Lock()
        self.directory.mkdir(parents=True, exist_ok=True)

    def find_match(self, prompt: str) -> Optional[dict[str, Any]]:
        entries = [entry for entry in self._load_entries() if (self.directory / str(entry.get("filename", ""))).is_file()]
        candidates = self._tfidf_candidates(prompt, entries)
        if not candidates:
            return None
        approved = self._rerank(prompt, candidates)
        if not approved:
            return None
        candidate_id, score = approved
        if score < self.match_threshold:
            return None
        selected = next((entry for entry in candidates if entry["id"] == candidate_id), None)
        if not selected:
            logger.warning("Music catalog reranker returned an unknown candidate ID.")
            return None
        return {**selected, "score": score, "path": self.directory / selected["filename"]}

    def add(self, source_path: Path, prompt: str, provider: str, model: str) -> dict[str, Any]:
        extension = source_path.suffix.lower() or ".mp3"
        entry = {"id": uuid.uuid4().hex, "filename": f"{uuid.uuid4().hex}{extension}", "prompt": prompt, "provider": provider, "model": model}
        with self._lock:
            shutil.copy2(source_path, self.directory / entry["filename"])
            entries = self._load_entries_unlocked()
            entries.append(entry)
            temporary = self.metadata_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(entries, indent=2), encoding="utf-8")
            os.replace(temporary, self.metadata_path)
        return entry

    def _tfidf_candidates(self, prompt: str, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        query = Counter(self._tokens(prompt))
        if not query:
            return []
        documents = [Counter(self._tokens(str(entry.get("prompt", "")))) for entry in entries]
        document_frequency = Counter(token for document in documents for token in document)
        doc_count = len(documents)

        def weight(token: str, count: int) -> float:
            return count * (math.log((doc_count + 1) / (document_frequency[token] + 1)) + 1.0)

        query_vector = {token: weight(token, count) for token, count in query.items()}
        query_norm = math.sqrt(sum(value * value for value in query_vector.values()))
        scored: list[tuple[float, dict[str, Any]]] = []
        for entry, document in zip(entries, documents):
            document_vector = {token: weight(token, count) for token, count in document.items()}
            denominator = query_norm * math.sqrt(sum(value * value for value in document_vector.values()))
            score = sum(query_vector.get(token, 0.0) * value for token, value in document_vector.items()) / denominator if denominator else 0.0
            if score > 0:
                scored.append((score, entry))
        return [entry for _, entry in sorted(scored, key=lambda item: item[0], reverse=True)[:self.candidate_count]]

    def _rerank(self, prompt: str, candidates: list[dict[str, Any]]) -> tuple[str, float] | None:
        if self._reranker:
            try:
                return self._reranker(prompt, candidates)
            except Exception as exc:
                logger.warning("Configured music reranker failed: %s", exc)
                return None
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            return None
        candidate_data = [{"id": entry["id"], "prompt": entry.get("prompt", "")} for entry in candidates]
        instruction = ("Choose the one candidate that would genuinely serve the requested instrumental background music. "
                       "Score semantic and musical fit from 0 to 1; do not select a merely related track. "
                       "Return only JSON: {\"id\": \"candidate id or empty\", \"score\": number}.")
        try:
            from google import genai
            response = genai.Client(api_key=api_key).models.generate_content(
                model=self.reranker_model,
                contents=f"{instruction}\n\nRequested prompt:\n{prompt}\n\nCandidates:\n{json.dumps(candidate_data)}",
                config={"response_mime_type": "application/json", "temperature": 0},
            )
            data = json.loads(response.text)
            return str(data.get("id", "")), max(0.0, min(1.0, float(data.get("score", -1))))
        except Exception as exc:
            logger.warning("Music catalog reranker unavailable: %s", exc)
            return None

    def _load_entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._load_entries_unlocked()

    def _load_entries_unlocked(self) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.metadata_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Ignoring unreadable music catalog metadata: %s", exc)
            return []

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return _TOKEN_RE.findall(text.lower())
