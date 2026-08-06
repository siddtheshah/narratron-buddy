import unittest
from unittest.mock import MagicMock

from services.suggestion_service import SuggestionItem, SuggestionService, SuggestionsResponse


class TestSuggestionService(unittest.TestCase):
    def setUp(self):
        self.service = SuggestionService(config={"suggestion_model": "gemini-2.5-flash"})

    def test_generate_suggestions_with_named_elements(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        
        expected_suggestions = SuggestionsResponse(
            suggestions=[
                SuggestionItem(
                    title="Explore Observatory Attic",
                    description="Mara investigates the hidden stellar dial upstairs.",
                    category="Action"
                ),
                SuggestionItem(
                    title="Storm Approaches",
                    description="Rain begins heavy drumming against the glass dome.",
                    category="Setting"
                )
            ]
        )
        mock_response.parsed = expected_suggestions
        mock_client.models.generate_content.return_value = mock_response

        named_elements = [
            {"name": "hero", "content": "Mara, a bold cartographer"},
            {"name": "setting", "content": "A flooded observatory"}
        ]

        result, fp = self.service.generate_suggestions(named_elements, theater_id="t1", client_override=mock_client)

        self.assertEqual(len(result.suggestions), 2)
        self.assertEqual(result.suggestions[0].title, "Explore Observatory Attic")
        self.assertEqual(result.suggestions[0].category, "Action")
        self.assertTrue(isinstance(fp, str) and len(fp) > 0)
        
        # Verify generate_content call
        mock_client.models.generate_content.assert_called_once()
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        self.assertEqual(call_kwargs["model"], "gemini-2.5-flash")
        self.assertIn("hero: Mara, a bold cartographer", call_kwargs["contents"])
        self.assertIn("setting: A flooded observatory", call_kwargs["contents"])

    def test_generate_suggestions_uses_cache_until_elements_update(self):
        mock_client = MagicMock()
        mock_response1 = MagicMock()
        mock_response1.parsed = SuggestionsResponse(
            suggestions=[SuggestionItem(title="Choice A", description="Desc A", category="Action")]
        )
        mock_response2 = MagicMock()
        mock_response2.parsed = SuggestionsResponse(
            suggestions=[SuggestionItem(title="Choice B", description="Desc B", category="Setting")]
        )
        mock_client.models.generate_content.side_effect = [mock_response1, mock_response2]

        named_elements_initial = [{"name": "hero", "content": "Mara"}]

        # First call: generates fresh suggestions via API
        res1, fp1 = self.service.generate_suggestions(named_elements_initial, theater_id="stage1", client_override=mock_client)
        self.assertEqual(res1.suggestions[0].title, "Choice A")
        self.assertEqual(mock_client.models.generate_content.call_count, 1)

        # Second call with SAME named elements: returns cached result without API call!
        res2, fp2 = self.service.generate_suggestions(named_elements_initial, theater_id="stage1", client_override=mock_client)
        self.assertEqual(res2.suggestions[0].title, "Choice A")
        self.assertEqual(fp1, fp2)
        self.assertEqual(mock_client.models.generate_content.call_count, 1)  # Still 1!

        # Third call with UPDATED named elements: invalidates cache & calls API for fresh suggestions!
        named_elements_updated = [
            {"name": "hero", "content": "Mara"},
            {"name": "item", "content": "Star map"}
        ]
        res3, fp3 = self.service.generate_suggestions(named_elements_updated, theater_id="stage1", client_override=mock_client)
        self.assertEqual(res3.suggestions[0].title, "Choice B")
        self.assertNotEqual(fp1, fp3)
        self.assertEqual(mock_client.models.generate_content.call_count, 2)  # Incremented to 2!

    def test_generate_suggestions_with_empty_elements(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        
        expected_suggestions = SuggestionsResponse(
            suggestions=[
                SuggestionItem(
                    title="Introduce a mysterious traveler",
                    description="A hooded figure enters carrying a glowing artifact.",
                    category="Character"
                )
            ]
        )
        mock_response.parsed = expected_suggestions
        mock_client.models.generate_content.return_value = mock_response

        result, _ = self.service.generate_suggestions([], theater_id="t2", client_override=mock_client)

        self.assertEqual(len(result.suggestions), 1)
        self.assertEqual(result.suggestions[0].title, "Introduce a mysterious traveler")

    def test_generate_suggestions_handles_json_text_response(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.parsed = None
        mock_response.text = '{"suggestions": [{"title": "Json Title", "description": "Json Desc", "category": "Plot Twist"}]}'
        mock_client.models.generate_content.return_value = mock_response

        result, _ = self.service.generate_suggestions([{"name": "item", "content": "Ancient key"}], theater_id="t3", client_override=mock_client)

        self.assertEqual(len(result.suggestions), 1)
        self.assertEqual(result.suggestions[0].title, "Json Title")
        self.assertEqual(result.suggestions[0].category, "Plot Twist")


if __name__ == "__main__":
    unittest.main()
