from pathlib import Path

from providers.hybrid_image_provider import HybridImageProvider
from providers.image_provider import ImageGenerationRequest, ImageGenerationResult, ImageProvider
from providers.local_image_prompt_classifier import HybridImageClassifier


class FakeProvider(ImageProvider):
    def __init__(self, provider_id: str):
        self.id = provider_id
        self.display_name = provider_id
        self.model = f"{provider_id}-model"
        self.calls = []

    def generate(self, request):
        self.calls.append(request)
        return ImageGenerationResult(b"image", "image/png", self.id, self.model)


def test_integrated_classifier_loads_exported_model():
    classifier = HybridImageClassifier()

    assert classifier.load_error is None
    assert classifier.model == "tfidf-char-logreg-v1"


def test_integrated_classifier_hard_rules_override_learned_model():
    classifier = HybridImageClassifier()

    result = classifier.classify("A dog carries a bag through a meadow")

    assert result["route_to_fallback"] is True
    assert result["reasons"] == ["creature_object_interaction"]


def test_integrated_classifier_approves_named_single_character():
    classifier = HybridImageClassifier()

    result = classifier.classify("Naruto Uzumaki wearing a turban, anime")

    assert result["route_to_fallback"] is False
    assert result["classifier_failed"] is False


def test_integrated_classifier_fails_closed_when_artifact_is_missing(tmp_path: Path):
    classifier = HybridImageClassifier(model_path=tmp_path / "missing.joblib")

    result = classifier.classify("A quiet mountain valley at sunrise")

    assert classifier.load_error is not None
    assert result["route_to_fallback"] is True
    assert result["classifier_failed"] is True


def test_hybrid_provider_routes_using_integrated_classifier():
    primary, fallback = FakeProvider("flux"), FakeProvider("gemini")
    provider = HybridImageProvider(primary, fallback)

    provider.generate(ImageGenerationRequest(prompt="Naruto Uzumaki wearing a turban, anime"))

    assert len(primary.calls) == 1
    assert not fallback.calls
