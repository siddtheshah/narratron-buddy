import unittest
from unittest.mock import MagicMock

from components.text_beautifier import (
    DEFAULT_BEAUTIFIER_MODEL,
    BeautifiedDialogueLine,
    BeautifiedSceneResponse,
    SingleTextBeautifyResponse,
    TextBeautifier,
    TextSpanEffect,
)


class TestTextBeautifier(unittest.TestCase):
    def test_default_initialization(self):
        beautifier = TextBeautifier()
        self.assertEqual(beautifier.model, DEFAULT_BEAUTIFIER_MODEL)
        self.assertEqual(beautifier.model, "gemini-3.5-flash-lite")

    def test_custom_model_and_config(self):
        beautifier = TextBeautifier(
            config={"text_beautifier_model": "custom-model"},
        )
        self.assertEqual(beautifier.model, "custom-model")

        beautifier_override = TextBeautifier(
            config={"text_beautifier_model": "custom-model"},
            model="override-model",
        )
        self.assertEqual(beautifier_override.model, "override-model")

    def test_argument_validation(self):
        beautifier = TextBeautifier()
        # Invalid type for beautify_text
        with self.assertRaises(TypeError):
            beautifier.beautify_text(123)  # type: ignore

        # Invalid type for beautify_scene
        with self.assertRaises(TypeError):
            beautifier.beautify_scene(123, [])  # type: ignore

        with self.assertRaises(TypeError):
            beautifier.beautify_scene("valid", "not-a-list")  # type: ignore

        with self.assertRaises(TypeError):
            beautifier.beautify_scene("valid", ["not-a-dict"])  # type: ignore

    def test_beautify_text_empty(self):
        beautifier = TextBeautifier()
        self.assertEqual(beautifier.beautify_text(""), [])
        self.assertEqual(beautifier.beautify_text("   "), [])

    def test_beautify_text_mocked_success(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.parsed = SingleTextBeautifyResponse(
            spans=[
                TextSpanEffect(text="The ancient chamber begins to ", effect="none", font="default"),
                TextSpanEffect(text="SHUDDER AND CRUMBLE!", effect="vibrate", font="cinematic", color="#ef4444"),
            ]
        )
        mock_client.models.generate_content.return_value = mock_response

        beautifier = TextBeautifier(client=mock_client)
        spans = beautifier.beautify_text("The ancient chamber begins to SHUDDER AND CRUMBLE!")

        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[0]["text"], "The ancient chamber begins to ")
        self.assertEqual(spans[0]["effect"], "none")
        self.assertEqual(spans[1]["text"], "SHUDDER AND CRUMBLE!")
        self.assertEqual(spans[1]["effect"], "vibrate")
        self.assertEqual(spans[1]["font"], "cinematic")
        self.assertEqual(spans[1]["color"], "#ef4444")

    def test_beautify_text_sanitization(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.parsed = SingleTextBeautifyResponse(
            spans=[
                TextSpanEffect(text="Text", effect="unknown_effect", font="unknown_font"),
            ]
        )
        mock_client.models.generate_content.return_value = mock_response

        beautifier = TextBeautifier(client=mock_client)
        spans = beautifier.beautify_text("Text")

        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["effect"], "none")
        self.assertEqual(spans[0]["font"], "default")

    def test_beautify_text_fallback_on_error(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("API unavailable")

        beautifier = TextBeautifier(client=mock_client)
        spans = beautifier.beautify_text("Watch out!")

        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["text"], "Watch out!")
        self.assertEqual(spans[0]["effect"], "none")

    def test_beautify_scene_empty(self):
        beautifier = TextBeautifier()
        res = beautifier.beautify_scene("", [])
        self.assertEqual(res["narration"], "")
        self.assertEqual(res["narration_spans"], [])
        self.assertEqual(res["dialogue"], [])

    def test_beautify_scene_mocked_success(self):
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.parsed = BeautifiedSceneResponse(
            narration_spans=[
                TextSpanEffect(text="An azure crystal ", effect="none"),
                TextSpanEffect(text="GLOWS WITH CELESTIAL RADIANCE", effect="scintillate", font="medieval", color="#38bdf8"),
            ],
            dialogue=[
                BeautifiedDialogueLine(
                    speaker="Elara",
                    text="Stand back, it's alive!",
                    kind="speech",
                    spans=[
                        TextSpanEffect(text="Stand back, ", effect="none"),
                        TextSpanEffect(text="it's alive!", effect="vibrate", font="bangers", color="#ef4444"),
                    ],
                )
            ],
        )
        mock_client.models.generate_content.return_value = mock_response

        beautifier = TextBeautifier(client=mock_client)
        result = beautifier.beautify_scene(
            narration="An azure crystal GLOWS WITH CELESTIAL RADIANCE",
            dialogue=[{"speaker": "Elara", "text": "Stand back, it's alive!", "kind": "speech"}],
        )

        self.assertEqual(result["narration"], "An azure crystal GLOWS WITH CELESTIAL RADIANCE")
        self.assertEqual(len(result["narration_spans"]), 2)
        self.assertEqual(result["narration_spans"][1]["effect"], "scintillate")
        self.assertEqual(result["narration_spans"][1]["font"], "medieval")

        self.assertEqual(len(result["dialogue"]), 1)
        self.assertEqual(result["dialogue"][0]["speaker"], "Elara")
        self.assertEqual(len(result["dialogue"][0]["spans"]), 2)
        self.assertEqual(result["dialogue"][0]["spans"][1]["effect"], "vibrate")
        self.assertEqual(result["dialogue"][0]["spans"][1]["font"], "bangers")

    def test_beautify_scene_fallback_on_error(self):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = RuntimeError("Quota exceeded")

        beautifier = TextBeautifier(client=mock_client)
        result = beautifier.beautify_scene(
            narration="A sudden quake shakes the ground.",
            dialogue=[{"speaker": "Garrick", "text": "Hold on!", "kind": "speech"}],
        )

        self.assertEqual(result["narration"], "A sudden quake shakes the ground.")
        self.assertEqual(len(result["narration_spans"]), 1)
        self.assertEqual(result["narration_spans"][0]["text"], "A sudden quake shakes the ground.")
        self.assertEqual(result["narration_spans"][0]["effect"], "none")

        self.assertEqual(len(result["dialogue"]), 1)
        self.assertEqual(len(result["dialogue"][0]["spans"]), 1)
        self.assertEqual(result["dialogue"][0]["spans"][0]["text"], "Hold on!")


if __name__ == "__main__":
    unittest.main()
