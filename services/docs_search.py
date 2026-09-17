"""In-memory full-text search for the public documentation pages."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from threading import RLock
from typing import Iterable

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion


_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class DocsSearchPage:
    title: str
    href: str
    html: str


@dataclass(frozen=True)
class _SearchChunk:
    page_title: str
    heading: str
    href: str
    text: str


class _MainContentParser(HTMLParser):
    """Extract heading-sized chunks from a rendered page's main element."""

    _SKIPPED_TAGS = {"script", "style", "nav", "aside"}
    _HEADING_TAGS = {"h1", "h2", "h3", "dt"}

    def __init__(self, page_title: str, href: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_title = page_title
        self.href = href
        self.chunks: list[_SearchChunk] = []
        self._main_depth = 0
        self._skip_depth = 0
        self._section_ids: list[str | None] = []
        self._heading_level = 0
        self._heading_parts: list[str] = []
        self._heading = page_title
        self._anchor: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "main":
            self._main_depth += 1
            return
        if not self._main_depth:
            return
        if tag in self._SKIPPED_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "section":
            self._section_ids.append(attributes.get("id"))
        if tag in self._HEADING_TAGS:
            self._flush_chunk()
            self._heading_level = int(tag[1]) if tag.startswith("h") else 4
            self._heading_parts = []
            self._anchor = attributes.get("id") or next(
                (section_id for section_id in reversed(self._section_ids) if section_id),
                None,
            )

    def handle_endtag(self, tag: str) -> None:
        if tag == "main" and self._main_depth:
            self._flush_chunk()
            self._main_depth -= 1
            return
        if not self._main_depth:
            return
        if tag in self._SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in self._HEADING_TAGS and self._heading_level:
            heading = _clean_text(" ".join(self._heading_parts))
            if heading:
                self._heading = heading
                self._parts.append(heading)
            self._heading_level = 0
            self._heading_parts = []
        if tag == "section" and self._section_ids:
            self._section_ids.pop()

    def handle_data(self, data: str) -> None:
        if not self._main_depth or self._skip_depth:
            return
        if self._heading_level:
            self._heading_parts.append(data)
        else:
            self._parts.append(data)

    def close(self) -> None:
        super().close()
        self._flush_chunk()

    def _flush_chunk(self) -> None:
        text = _clean_text(" ".join(self._parts))
        if text:
            href = f"{self.href}#{self._anchor}" if self._anchor else self.href
            self.chunks.append(
                _SearchChunk(
                    page_title=self.page_title,
                    heading=self._heading,
                    href=href,
                    text=text,
                )
            )
        self._parts = []


def _clean_text(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value).strip()


def _searchable_text(value: str) -> str:
    """Make code-style names searchable using ordinary words."""
    return _clean_text(re.sub(r"[_./-]+", " ", value))


class DocsSearchIndex:
    """A process-local search index rebuilt from rendered documentation pages."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._chunks: tuple[_SearchChunk, ...] = ()
        self._vectorizer: FeatureUnion | None = None
        self._matrix = None

    @property
    def document_count(self) -> int:
        with self._lock:
            return len(self._chunks)

    def build(self, pages: Iterable[DocsSearchPage]) -> int:
        chunks: list[_SearchChunk] = []
        for page in pages:
            parser = _MainContentParser(page.title, page.href)
            parser.feed(page.html)
            parser.close()
            chunks.extend(parser.chunks)

        vectorizer = FeatureUnion(
            [
                (
                    "words",
                    TfidfVectorizer(
                        lowercase=True,
                        ngram_range=(1, 2),
                        sublinear_tf=True,
                        strip_accents="unicode",
                    ),
                ),
                (
                    "characters",
                    TfidfVectorizer(
                        analyzer="char_wb",
                        lowercase=True,
                        ngram_range=(3, 5),
                        sublinear_tf=True,
                        strip_accents="unicode",
                    ),
                ),
            ],
            transformer_weights={"words": 0.75, "characters": 0.25},
        )
        corpus = [
            _searchable_text(f"{chunk.page_title} {chunk.heading} {chunk.text}")
            for chunk in chunks
        ]
        matrix = vectorizer.fit_transform(corpus) if corpus else None

        with self._lock:
            self._chunks = tuple(chunks)
            self._vectorizer = vectorizer if chunks else None
            self._matrix = matrix
        return len(chunks)

    def search(self, query: str, limit: int = 8) -> list[dict[str, object]]:
        clean_query = _clean_text(query)[:200]
        if not clean_query:
            return []

        with self._lock:
            chunks = self._chunks
            vectorizer = self._vectorizer
            matrix = self._matrix
            if not chunks or vectorizer is None or matrix is None:
                return []
            query_vector = vectorizer.transform([_searchable_text(clean_query)])
            scores = (matrix @ query_vector.T).toarray().ravel()

        query_lower = clean_query.casefold()
        ranked: list[tuple[float, int]] = []
        for index, (chunk, score) in enumerate(zip(chunks, scores)):
            heading_lower = chunk.heading.casefold()
            page_lower = chunk.page_title.casefold()
            adjusted_score = float(score)
            if query_lower in heading_lower:
                adjusted_score += 0.35
            elif query_lower in page_lower:
                adjusted_score += 0.2
            if adjusted_score >= 0.025:
                ranked.append((adjusted_score, index))
        ranked.sort(key=lambda item: item[0], reverse=True)

        results: list[dict[str, object]] = []
        seen_hrefs: set[str] = set()
        for score, index in ranked:
            chunk = chunks[index]
            if chunk.href in seen_hrefs:
                continue
            seen_hrefs.add(chunk.href)
            results.append(
                {
                    "title": chunk.heading,
                    "page_title": chunk.page_title,
                    "href": chunk.href,
                    "excerpt": self._excerpt(chunk.text, clean_query),
                    "score": round(score, 4),
                }
            )
            if len(results) >= max(1, min(limit, 20)):
                break
        return results

    @staticmethod
    def _excerpt(text: str, query: str, length: int = 180) -> str:
        if len(text) <= length:
            return text
        terms = [term.casefold() for term in re.findall(r"[\w-]+", query) if len(term) > 2]
        lower_text = text.casefold()
        positions = [lower_text.find(term) for term in terms]
        positions = [position for position in positions if position >= 0]
        center = min(positions) if positions else 0
        start = max(0, center - length // 3)
        end = min(len(text), start + length)
        start = 0 if start == 0 else text.find(" ", start) + 1
        end = len(text) if end == len(text) else text.rfind(" ", start, end)
        excerpt = text[start:end].strip()
        return f"{'…' if start else ''}{excerpt}{'…' if end < len(text) else ''}"


docs_search_index = DocsSearchIndex()
