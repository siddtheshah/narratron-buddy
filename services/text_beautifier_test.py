import unittest
from unittest.mock import MagicMock, patch

from providers.text_response_provider import (
    TextResponseProvider,
    TextResponseRequest,
    TextResponseResult,
    TextResponseProviderError,
)
from services.text_beautifier import (
    ALLOWED_EFFECTS,
    ALLOWED_FONTS,
    EFFECTS,
    FONTS,
    _BEAUTIFIER_PROMPT_TEMPLATE,
    _BEAUTIFY_SCENE_PROMPT_TEMPLATE,
    _BEAUTIFY_TEXT_PROMPT_TEMPLATE,
    BeautifiedDialogueLine,
    BeautifiedSceneResponse,
    SingleTextBeautifyResponse,
    TextBeautifier,
    TextSpanEffect,
    build_beautify_prompt,
    build_beautify_scene_prompt,
    build_beautify_text_prompt,
)


class MockTextProvider(TextResponseProvider):
    id = "mock-text"
    display_name = "Mock Text Provider"
    model = "mock-model"

    def __init__(self, model: str = "gemini-3.5-flash-lite"):
        self.model = model

    def generate(self, request: TextResponseRequest) -> TextResponseResult:
        return TextResponseResult(
            text="{}",
            provider=self.id,
            model=self.model,
            parsed=None,
        )


class TestTextBeautifier(unittest.TestCase):
    def setUp(self):
        self.mock_provider = MagicMock(spec=TextResponseProvider)
        self.mock_provider.model = "gemini-3.5-flash-lite"
        self.mock_provider.id = "gemini-mock"
        self.mock_provider.display_name = "Gemini Mock"

        self.get_provider_patcher = patch(
            "services.text_beautifier.get_text_response_provider",
            return_value=self.mock_provider,
        )
        self.mock_get_provider = self.get_provider_patcher.start()

    def tearDown(self):
        self.get_provider_patcher.stop()

    def test_initialization_validation(self):
        # Invalid config type raises TypeError
        with self.assertRaises(TypeError):
            TextBeautifier("not-a-dict")  # type: ignore

        # Valid default construction
        beautifier = TextBeautifier()
        self.assertEqual(beautifier.model, "gemini-3.5-flash-lite")
        self.mock_get_provider.assert_called_with("gemini-2-5", options={"model": "gemini-3.5-flash-lite"})

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

    def test_empty_text_returns_empty_spans(self):
        beautifier = TextBeautifier()
        self.assertEqual(beautifier.beautify_text(""), [])
        self.assertEqual(beautifier.beautify_text("   "), [])

    def test_beautify_text_with_mock_provider(self):
        self.mock_provider.generate.return_value = TextResponseResult(
            text="{}",
            provider="gemini",
            model="gemini-3.5-flash-lite",
            parsed=SingleTextBeautifyResponse(
                spans=[
                    TextSpanEffect(text="The ancient chamber begins to ", effect="none", font="default"),
                    TextSpanEffect(text="SHUDDER AND CRUMBLE!", effect="vibrate", font="bangers", color="#ef4444"),
                ]
            ),
        )

        beautifier = TextBeautifier()
        spans = beautifier.beautify_text("The ancient chamber begins to SHUDDER AND CRUMBLE!")

        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[0]["text"], "The ancient chamber begins to ")
        self.assertEqual(spans[0]["effect"], "none")
        self.assertEqual(spans[0]["font"], "default")

        self.assertEqual(spans[1]["text"], "SHUDDER AND CRUMBLE!")
        self.assertEqual(spans[1]["effect"], "vibrate")
        self.assertEqual(spans[1]["font"], "bangers")
        self.assertEqual(spans[1]["color"], "#ef4444")

        # Verify provider call arguments
        self.mock_provider.generate.assert_called_once()
        req: TextResponseRequest = self.mock_provider.generate.call_args[0][0]
        self.assertIn("The ancient chamber begins to SHUDDER AND CRUMBLE!", req.prompt)
        self.assertEqual(req.response_schema, SingleTextBeautifyResponse)

    def test_beautify_text_sanitizes_invalid_effects(self):
        self.mock_provider.generate.return_value = TextResponseResult(
            text="{}",
            provider="gemini",
            model="gemini-3.5-flash-lite",
            parsed=SingleTextBeautifyResponse(
                spans=[
                    TextSpanEffect(text="Text", effect="unknown_effect", font="unknown_font"),
                ]
            ),
        )

        beautifier = TextBeautifier()
        spans = beautifier.beautify_text("Text")
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["effect"], "none")
        self.assertEqual(spans[0]["font"], "default")

    def test_beautify_text_fallback_on_provider_error(self):
        self.mock_provider.generate.side_effect = TextResponseProviderError("Provider unavailable")

        beautifier = TextBeautifier()
        spans = beautifier.beautify_text("Watch out!")
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["text"], "Watch out!")
        self.assertEqual(spans[0]["effect"], "none")
        self.assertEqual(spans[0]["font"], "default")

    def test_beautify_scene_empty_returns_clean(self):
        beautifier = TextBeautifier()
        res = beautifier.beautify_scene("", [])
        self.assertEqual(res["narration"], "")
        self.assertEqual(res["narration_spans"], [])
        self.assertEqual(res["dialogue"], [])

    def test_beautify_scene_with_mock_provider(self):
        self.mock_provider.generate.return_value = TextResponseResult(
            text="{}",
            provider="gemini",
            model="gemini-3.5-flash-lite",
            parsed=BeautifiedSceneResponse(
                narration_spans=[
                    TextSpanEffect(text="The torch flickers.", effect="none"),
                    TextSpanEffect(text="A scream echoes!", effect="vibrate", font="creepster"),
                ],
                dialogue=[
                    BeautifiedDialogueLine(
                        speaker="Grim",
                        text="Did you hear that?",
                        kind="speech",
                        spans=[
                            TextSpanEffect(text="Did you hear that?", effect="pulse", font="cinematic"),
                        ],
                    )
                ],
            ),
        )

        beautifier = TextBeautifier()
        result = beautifier.beautify_scene(
            narration="The torch flickers. A scream echoes!",
            dialogue=[{"speaker": "Grim", "text": "Did you hear that?", "kind": "speech"}],
        )

        self.assertEqual(result["narration"], "The torch flickers. A scream echoes!")
        self.assertEqual(len(result["narration_spans"]), 2)
        self.assertEqual(result["narration_spans"][0]["effect"], "none")
        self.assertEqual(result["narration_spans"][1]["effect"], "vibrate")
        self.assertEqual(result["narration_spans"][1]["font"], "creepster")

        self.assertEqual(len(result["dialogue"]), 1)
        self.assertEqual(result["dialogue"][0]["speaker"], "Grim")
        self.assertEqual(len(result["dialogue"][0]["spans"]), 1)
        self.assertEqual(result["dialogue"][0]["spans"][0]["effect"], "pulse")

    def test_beautify_scene_fallback_on_error(self):
        self.mock_provider.generate.side_effect = Exception("Internal error")

        beautifier = TextBeautifier()
        result = beautifier.beautify_scene(
            narration="The storm rages.",
            dialogue=[{"speaker": "Captain", "text": "Hold on!", "kind": "speech"}],
        )

        self.assertEqual(result["narration"], "The storm rages.")
        self.assertEqual(len(result["narration_spans"]), 1)
        self.assertEqual(result["narration_spans"][0]["text"], "The storm rages.")
        self.assertEqual(result["narration_spans"][0]["effect"], "none")

        self.assertEqual(len(result["dialogue"]), 1)
        self.assertEqual(result["dialogue"][0]["spans"][0]["text"], "Hold on!")
        self.assertEqual(result["dialogue"][0]["spans"][0]["effect"], "none")

    def test_logging_in_beautify_text(self):
        self.mock_provider.generate.return_value = TextResponseResult(
            text="{}",
            provider="gemini",
            model="gemini-3.5-flash-lite",
            parsed=SingleTextBeautifyResponse(
                spans=[
                    TextSpanEffect(text="The ancient gate ", effect="none"),
                    TextSpanEffect(text="EXPLODES IN FIRE!", effect="flame", font="bangers"),
                ]
            ),
        )

        beautifier = TextBeautifier()
        with self.assertLogs("services.text_beautifier", level="INFO") as log_context:
            beautifier.beautify_text("The ancient gate EXPLODES IN FIRE!")

        log_output = "\n".join(log_context.output)
        self.assertIn("beautify_text starting", log_output)
        self.assertIn("beautify_text succeeded", log_output)
        self.assertIn("EXPLODES IN FIRE!", log_output)
        self.assertIn("flame/bangers", log_output)

    def test_logging_in_beautify_scene(self):
        self.mock_provider.generate.return_value = TextResponseResult(
            text="{}",
            provider="gemini",
            model="gemini-3.5-flash-lite",
            parsed=BeautifiedSceneResponse(
                narration_spans=[TextSpanEffect(text="Quiet night.", effect="none")],
                dialogue=[],
            ),
        )

        beautifier = TextBeautifier()
        with self.assertLogs("services.text_beautifier", level="INFO") as log_context:
            beautifier.beautify_scene("Quiet night.", [])

        log_output = "\n".join(log_context.output)
        self.assertIn("beautify_scene starting", log_output)
        self.assertIn("beautify_scene succeeded", log_output)

    def test_build_beautify_text_prompt(self):
        rendered = build_beautify_text_prompt("The cavern echoes.")
        self.assertIn("The cavern echoes.", rendered)
        self.assertIn("Available effects:", rendered)
        self.assertIn("Available fonts:", rendered)
        self.assertIn("'vibrate':", rendered)
        self.assertIn("'cinematic':", rendered)

    def test_build_beautify_scene_prompt(self):
        rendered = build_beautify_scene_prompt(
            narration="Deep inside the mountain.",
            dialogue=[{"speaker": "Theron", "text": "Halt!", "kind": "speech"}],
        )
        self.assertIn("NARRATION: Deep inside the mountain.", rendered)
        self.assertIn("[Theron] (speech): Halt!", rendered)
        self.assertIn("Available effects:", rendered)
        self.assertIn("Available fonts:", rendered)

    def test_single_prompt_template_conditional_stanza_selection(self):
        # When is_scene is False: renders text stanza, not scene stanza
        text_prompt = build_beautify_prompt(is_scene=False, text="A dark hallway awaits.")
        self.assertIn("A dark hallway awaits.", text_prompt)
        self.assertIn("Annotate the following text into sequential spans", text_prompt)
        self.assertNotIn("Scene Elements to Beautify:", text_prompt)

        # When is_scene is True: renders scene stanza, not text stanza
        scene_prompt = build_beautify_prompt(
            is_scene=True,
            narration="A dark hallway awaits.",
            dialogue=[{"speaker": "Guard", "text": "Who goes there?", "kind": "speech"}],
        )
        self.assertIn("Scene Elements to Beautify:", scene_prompt)
        self.assertIn("NARRATION: A dark hallway awaits.", scene_prompt)
        self.assertIn("[Guard] (speech): Who goes there?", scene_prompt)
        self.assertNotIn("Annotate the following text into sequential spans", scene_prompt)



if __name__ == "__main__":
    unittest.main()
