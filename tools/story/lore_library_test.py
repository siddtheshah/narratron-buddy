"""Unit tests for the shared, unbudgeted ``LoreLibrary``."""

import unittest
from unittest.mock import MagicMock, patch

from components.theater_manager import Theater
from tools.story.lore_library import LoreLibrary


class TestLoreLibrary(unittest.TestCase):
    def setUp(self) -> None:
        self.theater = MagicMock(spec=Theater)
        self.theater.theater_id = "library_theater"
        self.documents = [
            "read_premise.txt",
            "history/first_age.txt",
            "history/second_age.txt",
            "bestiary.txt",
        ]
        self.contents = {
            "read_premise.txt": "An eclipse hangs over Eldoria.",
            "history/first_age.txt": "The starblade was forged by the first king.",
            "history/second_age.txt": "A century of peace followed.",
            "bestiary.txt": "Shadow beasts hunt beneath the eclipse.",
        }
        self.theater.lore_documents.return_value = self.documents
        self.theater.read_lore_document.side_effect = self._read_document
        self.library = LoreLibrary(self.theater)

    def _read_document(self, document: str) -> str:
        try:
            return self.contents[document]
        except KeyError as exc:
            raise ValueError(f"Unknown lore document: {document}") from exc

    def test_requires_theater(self) -> None:
        with self.assertRaisesRegex(ValueError, "theater is required"):
            LoreLibrary(None)  # type: ignore[arg-type]

    def test_lists_available_documents(self) -> None:
        result = self.library.read_lore()

        self.assertIn("Available lore documents:", result)
        for document in self.documents:
            self.assertIn(f"- {document}", result)

    def test_limits_document_listing_without_limiting_calls(self) -> None:
        self.theater.lore_documents.return_value = [f"doc-{index}.txt" for index in range(5)]

        with patch("tools.story.lore_library.MAX_LORE_DOCUMENTS_LISTED", 2):
            first = self.library.read_lore()
            second = self.library.read_lore()

        self.assertIn("doc-0.txt", first)
        self.assertIn("doc-1.txt", first)
        self.assertNotIn("doc-2.txt", first)
        self.assertIn("[+3 additional documents omitted.]", first)
        self.assertEqual(second, first)

    def test_lists_documents_in_a_directory(self) -> None:
        result = self.library.read_lore("history")

        self.assertIn("Lore documents in 'history':", result)
        self.assertIn("history/first_age.txt", result)
        self.assertIn("history/second_age.txt", result)
        self.assertNotIn("bestiary.txt", result)

    def test_normalizes_windows_path_before_reading(self) -> None:
        result = self.library.read_lore("history\\first_age.txt")

        self.assertIn("The starblade was forged", result)
        self.theater.read_lore_document.assert_called_with("history/first_age.txt")

    def test_returns_read_errors_without_raising(self) -> None:
        result = self.library.read_lore("missing.txt")

        self.assertEqual(result, "Error: Unknown lore document: missing.txt")

    def test_truncates_long_document_content(self) -> None:
        self.contents["bestiary.txt"] = "abcdefghij"

        with patch("tools.story.lore_library.MAX_LORE_DOCUMENT_CONTEXT_CHARS", 5):
            result = self.library.read_lore("bestiary.txt")

        self.assertIn("abcde", result)
        self.assertNotIn("fghij", result)
        self.assertIn("[Excerpt truncated for planner context.]", result)

    def test_search_returns_matching_results_and_snippets(self) -> None:
        result = self.library.search_lore("starblade king")

        self.assertTrue(result.startswith("Lore search results for query 'starblade king':"))
        self.assertIn("history/first_age.txt", result)
        self.assertIn("Snippet:", result)

    def test_search_validates_query(self) -> None:
        self.assertEqual(self.library.search_lore(""), "Error: Search query cannot be empty.")
        self.assertEqual(
            self.library.search_lore("---"),
            "Error: Search query must contain alphanumeric keywords.",
        )

    def test_search_caches_index_and_query_results(self) -> None:
        first = self.library.search_lore("starblade")
        reads_after_first_search = self.theater.read_lore_document.call_count
        self.contents["history/first_age.txt"] = "The weapon has vanished."

        second = self.library.search_lore("starblade")

        self.assertEqual(second, first)
        self.assertEqual(
            self.theater.read_lore_document.call_count,
            reads_after_first_search,
        )

    def test_clear_cache_rebuilds_search_results(self) -> None:
        self.library.search_lore("starblade")
        reads_after_first_search = self.theater.read_lore_document.call_count
        self.contents["history/first_age.txt"] = "The weapon has vanished."

        self.library.clear_lore_cache()
        result = self.library.search_lore("starblade")

        self.assertEqual(
            result,
            "No matching lore documents found for query: 'starblade'",
        )
        self.assertGreater(
            self.theater.read_lore_document.call_count,
            reads_after_first_search,
        )

    def test_context_expands_read_prefixed_files_and_collapses_directories(self) -> None:
        result = self.library.get_lore_context()

        self.assertIn("read_premise.txt:\nAn eclipse hangs over Eldoria.", result)
        self.assertIn("history/ (directory)", result)
        self.assertIn("bestiary.txt", result)
        self.assertNotIn("history/first_age.txt", result)

    def test_repeated_reads_and_searches_have_no_consumer_budget(self) -> None:
        for _ in range(5):
            self.assertIn("Shadow beasts", self.library.read_lore("bestiary.txt"))
            self.assertIn("history/first_age.txt", self.library.search_lore("starblade"))

        self.assertFalse(hasattr(self.library, "deep_read_lore"))
        self.assertFalse(hasattr(self.library, "deep_search_lore"))
        self.assertFalse(hasattr(self.library, "reset_lore_call_counts"))


if __name__ == "__main__":
    unittest.main()
