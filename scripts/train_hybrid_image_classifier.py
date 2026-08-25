"""Train and export the in-process prompt-complexity classifier."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import FeatureUnion


CLASSIFIER_LABELS = {
    "multiple_creatures": "multiple_characters",
    "creature_object_interaction": "creature_object_interaction",
    "text_displayed": "text_displayed",
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("training_data/diffusiondb_routing_5000.csv"))
    parser.add_argument("--output", type=Path, default=Path("models/hybrid_image_classifier.joblib"))
    args = parser.parse_args()

    with args.input.open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    prompts = [row["prompt"] for row in rows]
    # Character n-grams generalize across inflections and misspellings common
    # in real image-generation prompts. Quote-aware word n-grams preserve
    # punctuation that char_wb drops, making a quoted title or sign available
    # to the text classifier as a feature.
    vectorizer = FeatureUnion([
        ("characters", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=30_000)),
        ("words", TfidfVectorizer(token_pattern=r'(?u)\b\w+\b|[\"“”]', ngram_range=(1, 2), min_df=1, sublinear_tf=True, max_features=20_000)),
    ])
    features = vectorizer.fit_transform(prompts)
    classifiers = {}
    for classifier_name, label_column in CLASSIFIER_LABELS.items():
        labels = [int(row[label_column]) for row in rows]
        if len(set(labels)) != 2:
            raise ValueError(f"Training data must contain both classes for {label_column}.")
        classifier = LogisticRegression(C=3.0, class_weight="balanced", max_iter=1_000, random_state=20260824)
        classifiers[classifier_name] = classifier.fit(features, labels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "vectorizer": vectorizer,
            "classifiers": classifiers,
            "model_version": "tfidf-char-word-ovr-logreg-v3",
            "training_rows": len(rows),
            # Favour recall: any independent classifier can select Gemini.
            "thresholds": {
                "multiple_creatures": 0.20,
                "creature_object_interaction": 0.20,
                "text_displayed": 0.20,
            },
        },
        args.output,
        compress=3,
    )
    print(f"Exported tfidf-char-word-ovr-logreg-v3 trained on {len(rows)} prompts to {args.output}")


if __name__ == "__main__":
    main()
