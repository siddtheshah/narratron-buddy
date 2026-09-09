"""Shared, unbudgeted theater lore reader and search index."""

from __future__ import annotations

import logging
import math
import re
from threading import Lock
from typing import Any, Dict, List, Optional, Tuple

from components.theater_manager import Theater

logger = logging.getLogger(__name__)

MAX_LORE_DOCUMENT_CONTEXT_CHARS = 12_000
MAX_LORE_DOCUMENTS_LISTED = 100


class LoreLibrary:
    """Encapsulate lore reading, indexing, caching, and searching."""

    def __init__(self, theater: Theater):
        if theater is None:
            raise ValueError("theater is required.")
        self.theater: Theater = theater
        self._theater_id = getattr(theater, "theater_id", None) or ""

        self._lore_index_cache: Optional[Dict[str, Dict[str, Any]]] = None
        self._lore_cache_lock: Lock = Lock()
        self._search_query_cache: Dict[str, str] = {}

    @property
    def theater_id(self) -> str:
        return self._theater_id or (getattr(self.theater, "theater_id", "") if self.theater else "")

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

    def get_lore_corpus_index(self) -> Dict[str, Dict[str, Any]]:
        """Return cached pre-tokenized lore corpus index for the active theater."""
        with self._lore_cache_lock:
            if self._lore_index_cache is not None:
                return self._lore_index_cache

            if not self.theater_id:
                return {}

            documents = self.theater.lore_documents()
            corpus_index: Dict[str, Dict[str, Any]] = {}
            for doc_path in documents:
                try:
                    content = self.theater.read_lore_document(doc_path)
                except Exception:
                    continue
                tokens = re.findall(r"\w+", content.lower())
                counts: Dict[str, int] = {}
                for t in tokens:
                    counts[t] = counts.get(t, 0) + 1
                corpus_index[doc_path] = {
                    "content": content,
                    "tokens": tokens,
                    "counts": counts,
                    "length": len(tokens),
                }
            self._lore_index_cache = corpus_index
            return self._lore_index_cache

    def clear_lore_cache(self) -> None:
        """Clear cached lore search index and cached query results."""
        with self._lore_cache_lock:
            self._lore_index_cache = None
            self._search_query_cache.clear()

    def read_lore(self, document: str = "") -> str:
        """List or read the theater's text-only lore documents.

        Call without ``document`` to list available ``.txt`` paths. Call again
        with one listed relative path to read it before planning characters or
        scenes.
        """
        if not self.theater_id:
            logger.warning("[LoreLibrary] Read lore called without active theater.")
            return "No theater is active, so no lore documents are available."

        if not document:
            documents = self.theater.lore_documents()
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
        if not clean_doc.lower().endswith(".txt"):
            prefix = clean_doc.rstrip("/") + "/"
            matching = [
                doc for doc in self.theater.lore_documents()
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

        corpus_index = self.get_lore_corpus_index()
        if not corpus_index:
            return "No lore documents are available for this theater."

        total_docs = len(corpus_index)
        unique_query_terms = list(dict.fromkeys(query_terms))
        df: Dict[str, int] = {}
        for t in unique_query_terms:
            df[t] = sum(1 for doc in corpus_index.values() if doc["counts"].get(t, 0) > 0)

        idf: Dict[str, float] = {}
        for t in unique_query_terms:
            idf[t] = math.log((total_docs + 1) / (df[t] + 1)) + 1.0

        scores: List[Tuple[str, float, str]] = []
        for doc_path, doc_info in corpus_index.items():
            length = doc_info["length"]
            if length == 0:
                continue
            counts = doc_info["counts"]
            score = 0.0
            for t in query_terms:
                tf = counts.get(t, 0) / length
                score += tf * idf[t]

            if score > 0:
                snippet = self.extract_snippet(doc_info["content"], unique_query_terms)
                scores.append((doc_path, score, snippet))

        scores.sort(key=lambda item: item[1], reverse=True)

        if not scores:
            res_str = f"No matching lore documents found for query: '{clean_query}'"
        else:
            results_lines = [f"Lore search results for query '{clean_query}':"]
            for doc_path, score, snippet in scores[:10]:
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

    def get_lore_context(self) -> str:
        """List top-level lore documents and directories for the planner context, automatically expanding files prefixed with 'read'."""
        if not self.theater_id:
            return ""
        documents = self.theater.lore_documents()
        if not documents:
            return ""
        top_level_files: list[str] = []
        top_level_dirs: set[str] = set()
        expanded_files: list[str] = []
        for doc in documents:
            parts = doc.split("/")
            filename = parts[-1]
            if filename.lower().startswith("read") or doc.lower().startswith("read"):
                content = self.theater.read_lore_document(doc)
                if len(content) > MAX_LORE_DOCUMENT_CONTEXT_CHARS:
                    content = (
                        content[:MAX_LORE_DOCUMENT_CONTEXT_CHARS]
                        + "\n[Excerpt truncated for planner context.]"
                    )
                expanded_files.append(f"- {doc}:\n{content}")
                if len(parts) > 1:
                    top_level_dirs.add(parts[0] + "/")
            else:
                if len(parts) == 1:
                    top_level_files.append(doc)
                else:
                    top_level_dirs.add(parts[0] + "/")
        items = (
            [f"- {d} (directory)" for d in sorted(top_level_dirs)]
            + [f"- {f}" for f in sorted(top_level_files)]
            + expanded_files
        )
        if not items:
            items = [f"- {doc}" for doc in documents[:MAX_LORE_DOCUMENTS_LISTED]]
        return "\n".join(items[:MAX_LORE_DOCUMENTS_LISTED])
