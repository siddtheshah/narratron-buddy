"""Shared, unbudgeted theater lore reader and search index."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import logging
import math
import re
from threading import Lock
from typing import List, Literal
from pathlib import PurePosixPath

from components.theater_manager import Theater

logger = logging.getLogger(__name__)

MAX_LORE_DOCUMENT_CONTEXT_CHARS = 12_000
MAX_LORE_DOCUMENTS_LISTED = 100
StoryRole = Literal["responder", "planner", "recorder", "game"]
ROLE_DOCUMENTS: dict[StoryRole, str] = {
    "responder": "RESPONDER.md",
    "planner": "PLANNER.md",
    "recorder": "RECORDER.md",
    "game": "GAME.md",
}
ROLE_FILENAMES = {name.casefold() for name in ROLE_DOCUMENTS.values()}

# Turn queries are prose, unlike the short keyword queries sent to search_lore.
PROSE_STOP_WORDS = frozenset(
    "a an and are as at be been but by can could did do does for from had has have "
    "he her him his how i if in into is it its me my of on or our she so than that "
    "the their them then there these they this those to up us was we were what "
    "when where which who will with would you your".split()
)


@dataclass(frozen=True)
class _IndexedLoreDocument:
    content: str
    counts: Counter[str]
    length: int


@dataclass(frozen=True)
class _LoreCorpus:
    documents: dict[str, _IndexedLoreDocument]
    document_frequencies: Counter[str]

    def inverse_document_frequency(self, term: str) -> float:
        return math.log((len(self.documents) + 1) / (self.document_frequencies[term] + 1)) + 1.0


@dataclass(frozen=True)
class LoreMatch:
    document: str
    score: float
    hit_rate: float
    snippet: str


class LoreLibrary:
    """Encapsulate lore reading, indexing, caching, and searching."""

    def __init__(self, theater: Theater) -> None:
        if theater is None:
            raise ValueError("theater is required.")
        self.theater: Theater = theater

        self._lore_index_cache: _LoreCorpus | None = None
        self._lore_cache_lock: Lock = Lock()
        self._search_query_cache: dict[str, str] = {}

    def _visible_documents(self, role: StoryRole | None = None) -> list[str]:
        """Keep role instructions out of other consumers' lists and search indexes."""
        own_document = ROLE_DOCUMENTS[role] if role is not None else None
        return [
            path for path in self.theater.lore_documents()
            if PurePosixPath(path).name.casefold() not in ROLE_FILENAMES
            or path == own_document
        ]

    @property
    def theater_id(self) -> str:
        return self.theater.theater_id

    @staticmethod
    def extract_snippet(content: str, terms: List[str], max_len: int = 150) -> str:
        """Extract a short representative snippet around the first occurrence of any query term."""
        content_lower = content.lower()
        earliest_idx = -1
        for t in terms:
            idx = content_lower.find(t)
            if idx != -1:
                if earliest_idx == -1 or idx < earliest_idx:
                    earliest_idx = idx

        if earliest_idx == -1:
            snippet = content[:max_len].replace("\n", " ").strip()
            return f'"{snippet}..."' if len(content) > max_len else f'"{snippet}"'

        start = max(0, earliest_idx - 30)
        end = min(len(content), earliest_idx + max_len - 30)
        snippet = content[start:end].replace("\n", " ").strip()
        prefix = "..." if start > 0 else ""
        suffix = "..." if end < len(content) else ""
        return f'"{prefix}{snippet}{suffix}"'

    def _get_lore_corpus(self) -> _LoreCorpus:
        """Return cached pre-tokenized lore corpus index for the active theater."""
        with self._lore_cache_lock:
            if self._lore_index_cache is not None:
                return self._lore_index_cache

            documents: dict[str, _IndexedLoreDocument] = {}
            document_frequencies: Counter[str] = Counter()
            for doc_path in self._visible_documents():
                try:
                    content = self.theater.read_lore_document(doc_path)
                except Exception:
                    continue
                tokens = re.findall(r"\w+", content.lower())
                counts = Counter(tokens)
                documents[doc_path] = _IndexedLoreDocument(content, counts, len(tokens))
                document_frequencies.update(counts.keys())
            self._lore_index_cache = _LoreCorpus(documents, document_frequencies)
            return self._lore_index_cache

    @staticmethod
    def _rank_documents(
        corpus: _LoreCorpus, query_terms: list[str], idf: dict[str, float],
    ) -> list[tuple[str, float]]:
        scores: list[tuple[str, float]] = []
        for path, document in corpus.documents.items():
            if document.length == 0:
                continue
            score = sum(document.counts[term] / document.length * idf[term] for term in query_terms)
            if score > 0:
                scores.append((path, score))
        return sorted(scores, key=lambda item: item[1], reverse=True)

    def match_lore(self, text: str, *, limit: int) -> list[LoreMatch]:
        """Rank shared lore for prose and report IDF-weighted query term coverage."""
        terms = list(dict.fromkeys(
            term for term in re.findall(r"\w+", text.lower()) if term not in PROSE_STOP_WORDS
        ))
        if not terms or not self.theater_id:
            return []

        corpus = self._get_lore_corpus()
        idf = {term: corpus.inverse_document_frequency(term) for term in terms}
        total_query_weight = sum(idf.values())
        ranked = self._rank_documents(corpus, terms, idf)
        matches: list[LoreMatch] = []
        for path, score in ranked[:limit]:
            document = corpus.documents[path]
            matched_terms = [term for term in terms if document.counts[term]]
            hit_rate = sum(idf[term] for term in matched_terms) / total_query_weight
            strongest_term = max(matched_terms, key=idf.__getitem__)
            matches.append(LoreMatch(
                path, score, hit_rate, self.extract_snippet(document.content, [strongest_term]),
            ))
        return matches

    def clear_lore_cache(self) -> None:
        """Clear cached lore search index and cached query results."""
        with self._lore_cache_lock:
            self._lore_index_cache = None
            self._search_query_cache.clear()

    def read_lore(self, document: str = "", *, role: StoryRole | None = None) -> str:
        """List or read the theater's text-only lore documents.

        Call without ``document`` to list available text paths. Call again
        with one listed relative path to read it before planning characters or
        scenes.
        """
        if not self.theater_id:
            logger.warning("[LoreLibrary] Read lore called without active theater.")
            return "No theater is active, so no lore documents are available."

        if not document:
            documents = self._visible_documents(role)
            listed_documents = documents[:MAX_LORE_DOCUMENTS_LISTED]
            omission = (
                f"\n[+{len(documents) - len(listed_documents)} additional documents omitted.]"
                if len(documents) > len(listed_documents)
                else ""
            )
            logger.debug(
                "[LoreLibrary] Listing lore documents for theater=%s (total=%d, listed=%d)",
                self.theater_id,
                len(documents),
                len(listed_documents),
            )
            return (
                ("Available lore documents:\n" + "\n".join(f"- {path}" for path in listed_documents) + omission
                if documents
                else "No lore documents are available for this theater.")
            )

        clean_doc = str(document or "").strip().replace("\\", "/")
        normalized_doc = PurePosixPath(clean_doc).as_posix()
        if PurePosixPath(clean_doc).name.casefold() in ROLE_FILENAMES:
            if role is None or normalized_doc.casefold() != ROLE_DOCUMENTS[role].casefold():
                return "Error: Role instructions are private to their respective story module."
        if not clean_doc.lower().endswith((".txt", ".md")):
            prefix = clean_doc.rstrip("/") + "/"
            matching = [
                doc for doc in self._visible_documents(role)
                if doc.startswith(prefix)
            ]
            if matching:
                listed_matching = matching[:MAX_LORE_DOCUMENTS_LISTED]
                omission = (
                    f"\n[+{len(matching) - len(listed_matching)} additional documents omitted.]"
                    if len(matching) > len(listed_matching)
                    else ""
                )
                logger.debug(
                    "[LoreLibrary] Reading lore directory '%s' for theater=%s (matching=%d)",
                    clean_doc,
                    self.theater_id,
                    len(matching),
                )
                return (
                    f"Lore documents in '{clean_doc}':\n"
                    + "\n".join(f"- {path}" for path in listed_matching)
                    + omission
                )
        try:
            content = self.theater.read_lore_document(clean_doc)
        except ValueError as error:
            logger.warning(
                "[LoreLibrary] Failed to read lore document '%s' for theater=%s: %s",
                clean_doc,
                self.theater_id,
                error,
            )
            return f"Error: {error}"
        logger.debug(
            "[LoreLibrary] Read lore document '%s' for theater=%s (chars=%d)",
            clean_doc,
            self.theater_id,
            len(content),
        )
        excerpt = content[:MAX_LORE_DOCUMENT_CONTEXT_CHARS]
        suffix = "\n[Excerpt truncated for planner context.]" if len(content) > len(excerpt) else ""
        return f"Lore document: {clean_doc}\n\n{excerpt}{suffix}"

    def search_lore(self, query: str) -> str:
        """Perform a keyword search (TF-IDF based) across all text lore documents in the theater."""
        if not self.theater_id:
            logger.warning("[LoreLibrary] Search lore called without active theater.")
            return "No theater is active, so no lore documents are available."

        clean_query = str(query or "").strip()
        if not clean_query:
            return "Error: Search query cannot be empty."

        query_terms = re.findall(r"\w+", clean_query.lower())
        if not query_terms:
            return "Error: Search query must contain alphanumeric keywords."

        query_key = " ".join(query_terms)
        with self._lore_cache_lock:
            cached_result = self._search_query_cache.get(query_key)
        if cached_result is not None:
            logger.debug(
                "[LoreLibrary] Using cached search_lore result for query='%s' in theater=%s",
                clean_query,
                self.theater_id,
            )
            return cached_result

        corpus = self._get_lore_corpus()
        if not corpus.documents:
            return "No lore documents are available for this theater."

        unique_query_terms = list(dict.fromkeys(query_terms))
        idf = {term: corpus.inverse_document_frequency(term) for term in unique_query_terms}
        scores = self._rank_documents(corpus, query_terms, idf)

        if not scores:
            res_str = f"No matching lore documents found for query: '{clean_query}'"
        else:
            results_lines = [f"Lore search results for query '{clean_query}':"]
            for doc_path, score in scores[:10]:
                snippet = self.extract_snippet(corpus.documents[doc_path].content, unique_query_terms)
                results_lines.append(f"- {doc_path} (score: {score:.4f})")
                if snippet:
                    results_lines.append(f"  Snippet: {snippet}")
            res_str = "\n".join(results_lines)

        with self._lore_cache_lock:
            self._search_query_cache[query_key] = res_str

        logger.debug(
            "[LoreLibrary] Searched lore for query='%s' in theater=%s (matches=%d)",
            clean_query,
            self.theater_id,
            len(scores),
        )

        return res_str

    def get_lore_context(self, *, role: StoryRole | None = None) -> str:
        """Pin the complete role guide, followed by the shared lore catalog."""
        if not self.theater_id:
            return ""
        documents = self._visible_documents(role)
        if not documents:
            return ""
        top_level_files: list[str] = []
        top_level_dirs: set[str] = set()
        expanded_files: list[str] = []
        for doc in documents:
            parts = doc.split("/")
            if role is not None and doc == ROLE_DOCUMENTS[role]:
                content = self.theater.read_lore_document(doc)
                expanded_files.append(f"# Pinned {role.title()} Instructions ({doc})\n{content}")
            else:
                if len(parts) == 1:
                    top_level_files.append(doc)
                else:
                    top_level_dirs.add(parts[0] + "/")
        items = (
            [f"- {d} (directory)" for d in sorted(top_level_dirs)]
            + [f"- {f}" for f in sorted(top_level_files)]
        )
        return "\n".join(expanded_files + items[:MAX_LORE_DOCUMENTS_LISTED])
