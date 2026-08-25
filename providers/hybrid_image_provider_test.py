import logging

from providers.hybrid_image_provider import HybridImageProvider
from providers.image_provider import ImageGenerationRequest, ImageGenerationResult, ImageProvider, ImageReference
from providers.local_image_prompt_classifier import DISTILLED_ROUTING_EXAMPLES, LocalImagePromptClassifier


class FakeProvider(ImageProvider):
    def __init__(self, provider_id: str):
        self.id = provider_id
        self.display_name = provider_id
        self.model = f"{provider_id}-model"
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        return ImageGenerationResult(b"image", "image/png", self.id, self.model, usage={"source": self.id})


class AssertNoCallTextProvider:
    """Compatibility fixture: the local router must never invoke this."""

    id = "remote-classifier"

    def generate(self, request):
        raise AssertionError("routing must not call a remote text provider")


def test_hybrid_routes_interaction_to_fallback_and_records_decision():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    provider = HybridImageProvider(primary, fallback)

    result = provider.generate(ImageGenerationRequest(prompt="dog carries a bag"))

    assert not primary.calls and len(fallback.calls) == 1
    assert result.usage["routing"]["reasons"] == ["creature_object_interaction"]
    assert result.usage["routing"]["classifier_model"] == "tfidf-char-word-ovr-logreg-v3"


def test_hybrid_uses_flux_only_for_an_eligible_simple_scene():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    provider = HybridImageProvider(primary, fallback)

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


def test_hybrid_allows_context_sensitive_words_without_a_primary_signal():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    provider = HybridImageProvider(primary, fallback)

    result = provider.generate(ImageGenerationRequest(prompt="A floating city in the sky beside a navigational compass on a map."))

    assert len(primary.calls) == 1 and not fallback.calls
    assert result.usage["routing"]["ambiguous_terms"] == []


def test_hybrid_never_calls_legacy_remote_classifier_argument():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    provider = HybridImageProvider(primary, fallback, text_provider=AssertNoCallTextProvider())

    provider.generate(ImageGenerationRequest(prompt="A lonely desert road under stars"))

    assert len(primary.calls) == 1 and not fallback.calls


def test_hybrid_logging_emits_expected_prefix(caplog):
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    with caplog.at_level(logging.DEBUG):
        provider = HybridImageProvider(primary, fallback)
        provider.generate(ImageGenerationRequest(prompt="A calm sunrise over misty hills"))

    records = [rec for rec in caplog.records if rec.name == "providers.hybrid_image_provider"]
    assert any("[HybridImageProvider]" in rec.getMessage() for rec in records)
    assert any("Local classifier decision" in rec.getMessage() for rec in records)
    assert any("Routing decision" in rec.getMessage() for rec in records)


def test_distilled_examples_are_all_classified_as_rated():
    classifier = LocalImagePromptClassifier()

    assert len(DISTILLED_ROUTING_EXAMPLES) == 40
    for example in DISTILLED_ROUTING_EXAMPLES:
        result = classifier.classify(example.prompt)
        assert result["route_to_fallback"] is example.route_to_fallback, example.prompt
        assert result["scene_type"] == example.scene_type, example.prompt
