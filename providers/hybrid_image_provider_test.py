from providers.hybrid_image_provider import HybridImageProvider
from providers.image_provider import ImageGenerationRequest, ImageGenerationResult, ImageProvider, ImageReference


class FakeProvider(ImageProvider):
    def __init__(self, provider_id: str):
        self.id = provider_id
        self.display_name = provider_id
        self.model = f"{provider_id}-model"
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        return ImageGenerationResult(b"image", "image/png", self.id, self.model, usage={"source": self.id})


def test_hybrid_routes_interaction_to_fallback_and_records_decision():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    provider = HybridImageProvider(primary, fallback, classifier=lambda prompt: {"route_to_fallback": True, "reasons": ["creature_object_interaction"]})

    result = provider.generate(ImageGenerationRequest(prompt="dog carries a bag"))

    assert not primary.calls and len(fallback.calls) == 1
    assert result.provider == "hybrid-flux-gemini"
    assert result.usage["routing"]["selected_provider"] == "gemini"


def test_hybrid_fails_closed_to_gemini_when_classifier_errors():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    provider = HybridImageProvider(primary, fallback, classifier=lambda prompt: (_ for _ in ()).throw(RuntimeError("down")))

    result = provider.generate(ImageGenerationRequest(prompt="a moonlit observatory"))

    assert not primary.calls and len(fallback.calls) == 1
    assert result.usage["routing"]["classifier_failed"] is True


def test_hybrid_uses_flux_only_for_an_eligible_simple_scene():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    provider = HybridImageProvider(
        primary,
        fallback,
        classifier=lambda prompt: {"route_to_fallback": False, "scene_type": "pure_environment"},
    )

    provider.generate(ImageGenerationRequest(prompt="mist over a quiet mountain valley"))

    assert len(primary.calls) == 1 and not fallback.calls


def test_hybrid_routes_reference_guided_images_to_gemini_without_classifying():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    classifier = lambda prompt: (_ for _ in ()).throw(AssertionError("references must bypass classification"))
    provider = HybridImageProvider(primary, fallback, classifier=classifier)

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
    provider = HybridImageProvider(
        primary,
        fallback,
        classifier=lambda prompt: {
            "route_to_fallback": True,
            "reasons": ["contextual_disambiguation"],
            "ambiguous_terms": ["floating", "compass"],
        },
    )

    result = provider.generate(ImageGenerationRequest(prompt="A floating city in the sky beside a navigational compass on a map."))

    assert not primary.calls and len(fallback.calls) == 1
    assert result.usage["routing"]["ambiguous_terms"] == ["floating", "compass"]
