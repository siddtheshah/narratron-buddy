from pathlib import Path

import pytest

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


class StubVectorizer:
    def transform(self, prompts):
        return prompts


class StubClassifier:
    def __init__(self, probability: float):
        self.probability = probability

    def predict_proba(self, features):
        return [[1.0 - self.probability, self.probability]]


def test_integrated_classifier_loads_exported_model():
    classifier = HybridImageClassifier()

    assert classifier.load_error is None
    assert classifier.model == "tfidf-char-word-ovr-logreg-v3"
    assert set(classifier._artifact["classifiers"]) == {
        "multiple_creatures",
        "creature_object_interaction",
        "text_displayed",
    }


def test_integrated_classifier_hard_rules_override_learned_model():
    classifier = HybridImageClassifier()

    result = classifier.classify("A dog carries a bag through a meadow")

    assert result["route_to_fallback"] is True
    assert result["reasons"] == ["creature_object_interaction"]


def test_integrated_classifier_routes_quoted_phrase_as_displayed_text():
    classifier = HybridImageClassifier()

    result = classifier.classify('A book cover titled "The Last Voyage"')

    assert result["route_to_fallback"] is True
    assert result["reasons"] == ["text_rendering"]


@pytest.mark.parametrize(
    ("classifier_name", "expected_reason"),
    [
        ("multiple_creatures", "multiple_subjects"),
        ("creature_object_interaction", "creature_object_interaction"),
        ("text_displayed", "text_rendering"),
    ],
)
def test_any_independent_classifier_routes_to_gemini(classifier_name, expected_reason):
    classifier = HybridImageClassifier()
    classifier._artifact = {
        "vectorizer": StubVectorizer(),
        "classifiers": {
            name: StubClassifier(0.21 if name == classifier_name else 0.0)
            for name in ("multiple_creatures", "creature_object_interaction", "text_displayed")
        },
        "thresholds": {
            "multiple_creatures": 0.20,
            "creature_object_interaction": 0.20,
            "text_displayed": 0.20,
        },
    }

    result = classifier.classify("a quiet mountain valley")

    assert result["route_to_fallback"] is True
    assert result["reasons"] == [expected_reason]


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
