import json
import logging

from providers.hybrid_image_provider import CLASSIFIER_INSTRUCTIONS, HybridImageProvider, ImageClassifierResponse
from providers.image_provider import ImageGenerationRequest, ImageGenerationResult, ImageProvider, ImageReference
from providers.text_response_provider import TextResponseProvider, TextResponseResult


class FakeProvider(ImageProvider):
    def __init__(self, provider_id: str):
        self.id = provider_id
        self.display_name = provider_id
        self.model = f"{provider_id}-model"
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        return ImageGenerationResult(b"image", "image/png", self.id, self.model, usage={"source": self.id})


class FakeTextProvider(TextResponseProvider):
    id = "fake-text-classifier"
    display_name = "Fake Text Classifier"
    model = "gemini-2.5-flash-lite"

    def __init__(self, response_text: str = "", parsed=None):
        self.response_text = response_text
        self.parsed = parsed
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        parsed = self.parsed
        if parsed is None and request.response_schema is not None and self.response_text:
            try:
                parsed = self.validate_structured_response(request.response_schema, self.response_text)
            except Exception:
                parsed = None
        return TextResponseResult(
            text=self.response_text,
            provider=self.id,
            model=self.model,
            parsed=parsed,
        )


class ErrorTextProvider(TextResponseProvider):
    id = "error-text-classifier"
    display_name = "Error Text Classifier"
    model = "gemini-2.5-flash-lite"

    def generate(self, request):
        raise RuntimeError("down")


class AssertNoCallTextProvider(TextResponseProvider):
    id = "no-call-text-classifier"
    display_name = "No Call Text Classifier"
    model = "gemini-2.5-flash-lite"

    def generate(self, request):
        raise AssertionError("references must bypass classification")


def test_hybrid_routes_interaction_to_fallback_and_records_decision():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    text_provider = FakeTextProvider(json.dumps({"route_to_fallback": True, "reasons": ["creature_object_interaction"]}))
    provider = HybridImageProvider(primary, fallback, text_provider=text_provider)

    result = provider.generate(ImageGenerationRequest(prompt="dog carries a bag"))

    assert not primary.calls and len(fallback.calls) == 1
    assert result.provider == "hybrid-flux-gemini"
    assert result.usage["routing"]["selected_provider"] == "gemini"


def test_hybrid_fails_closed_to_gemini_when_classifier_errors():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    provider = HybridImageProvider(primary, fallback, text_provider=ErrorTextProvider())

    result = provider.generate(ImageGenerationRequest(prompt="a moonlit observatory"))

    assert not primary.calls and len(fallback.calls) == 1
    assert result.usage["routing"]["classifier_failed"] is True


def test_hybrid_uses_flux_only_for_an_eligible_simple_scene():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    text_provider = FakeTextProvider(json.dumps({"route_to_fallback": False, "scene_type": "pure_environment"}))
    provider = HybridImageProvider(primary, fallback, text_provider=text_provider)

    provider.generate(ImageGenerationRequest(prompt="mist over a quiet mountain valley"))

    assert len(primary.calls) == 1 and not fallback.calls


def test_hybrid_routes_reference_guided_images_to_gemini_without_classifying():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    provider = HybridImageProvider(primary, fallback, text_provider=AssertNoCallTextProvider())

    result = provider.generate(
        ImageGenerationRequest(
            prompt="the hero at dawn",
            references=[ImageReference(name="hero.png", data=b"reference", mime_type="image/png")],
        )
    )
    assert not primary.calls and len(fallback.calls) == 1
    assert result.usage["routing"]["reasons"] == ["reference_images"]


def test_hybrid_routes_context_sensitive_words_to_fallback():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    text_provider = FakeTextProvider(
        json.dumps({
            "route_to_fallback": True,
            "reasons": ["contextual_disambiguation"],
            "ambiguous_terms": ["floating", "compass"],
        })
    )
    provider = HybridImageProvider(primary, fallback, text_provider=text_provider)

    result = provider.generate(ImageGenerationRequest(prompt="A floating city in the sky beside a navigational compass on a map."))

    assert not primary.calls and len(fallback.calls) == 1
    assert result.usage["routing"]["ambiguous_terms"] == ["floating", "compass"]


def test_hybrid_logging_emits_expected_prefix(caplog):
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    text_provider = FakeTextProvider(json.dumps({"route_to_fallback": False, "scene_type": "pure_environment"}))
    with caplog.at_level(logging.DEBUG):
        provider = HybridImageProvider(primary, fallback, text_provider=text_provider)
        provider.generate(ImageGenerationRequest(prompt="A calm sunrise over misty hills"))

    records = [rec for rec in caplog.records if rec.name == "providers.hybrid_image_provider"]
    assert any("[HybridImageProvider]" in rec.getMessage() for rec in records)
    assert any("generate() called" in rec.getMessage() for rec in records)
    assert any("Routing decision" in rec.getMessage() for rec in records)


def test_hybrid_uses_text_response_provider_for_classification():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    json_response = '{"route_to_fallback": false, "scene_type": "pure_environment", "reasons": [], "ambiguous_terms": []}'
    text_provider = FakeTextProvider(json_response)
    provider = HybridImageProvider(primary, fallback, text_provider=text_provider)

    result = provider.generate(ImageGenerationRequest(prompt="A lonely desert road under stars"))

    assert len(primary.calls) == 1 and not fallback.calls
    assert len(text_provider.requests) == 1
    assert "A lonely desert road under stars" in text_provider.requests[0].prompt
    assert result.usage["routing"]["selected_provider"] == "flux"
    assert result.usage["routing"]["scene_type"] == "pure_environment"


def test_hybrid_handles_markdown_fenced_json_from_text_provider():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    fenced_json = '```json\n{"route_to_fallback": true, "scene_type": "complex", "reasons": ["text_rendering"], "ambiguous_terms": []}\n```'
    text_provider = FakeTextProvider(fenced_json)
    provider = HybridImageProvider(primary, fallback, text_provider=text_provider)

    result = provider.generate(ImageGenerationRequest(prompt="A storefront with the words 'OPEN NOW'"))

    assert not primary.calls and len(fallback.calls) == 1
    assert result.usage["routing"]["selected_provider"] == "gemini"
    assert "text_rendering" in result.usage["routing"]["reasons"]


def test_hybrid_passes_structured_response_schema_in_request():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    text_provider = FakeTextProvider(
        json.dumps({"route_to_fallback": False, "scene_type": "single_character", "reasons": [], "ambiguous_terms": []})
    )
    provider = HybridImageProvider(primary, fallback, text_provider=text_provider)

    result = provider.generate(ImageGenerationRequest(prompt="A lonely wanderer standing on a sand dune"))

    assert len(primary.calls) == 1 and not fallback.calls
    assert len(text_provider.requests) == 1
    assert text_provider.requests[0].response_schema is ImageClassifierResponse
    assert result.usage["routing"]["scene_type"] == "single_character"


def test_hybrid_handles_parsed_pydantic_instance_from_text_provider():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    parsed_model = ImageClassifierResponse(
        route_to_fallback=False,
        scene_type="pure_environment",
        reasons=[],
        ambiguous_terms=[],
    )
    text_provider = FakeTextProvider(response_text="", parsed=parsed_model)
    provider = HybridImageProvider(primary, fallback, text_provider=text_provider)

    result = provider.generate(ImageGenerationRequest(prompt="Emerald waterfall in a jungle valley"))

    assert len(primary.calls) == 1 and not fallback.calls
    assert result.usage["routing"]["selected_provider"] == "flux"
    assert result.usage["routing"]["scene_type"] == "pure_environment"


def test_classifier_instructions_do_not_contain_json_blob():
    assert "{" not in CLASSIFIER_INSTRUCTIONS
    assert "}" not in CLASSIFIER_INSTRUCTIONS
    assert "Return JSON only with:" not in CLASSIFIER_INSTRUCTIONS




