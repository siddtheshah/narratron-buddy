"""Train and export the in-process prompt-complexity classifier."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("training_data/diffusiondb_routing_5000.csv"))
    parser.add_argument("--output", type=Path, default=Path("models/hybrid_image_classifier.joblib"))
    args = parser.parse_args()

    with args.input.open(encoding="utf-8", newline="") as input_file:
        rows = list(csv.DictReader(input_file))
    prompts = [row["prompt"] for row in rows]
    labels = [int(row["complex"]) for row in rows]
    if len(set(labels)) != 2:
        raise ValueError("Training data must contain both complex and non-complex prompts.")

    # Word n-grams capture prompt concepts; character n-grams generalize across
    # inflections and misspellings common in real image-generation prompts.
    model = Pipeline([
        ("features", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True, max_features=30_000)),
        ("classifier", LogisticRegression(C=3.0, class_weight="balanced", max_iter=1_000, random_state=20260824)),
    ])
    model.fit(prompts, labels)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "model_version": "tfidf-char-logreg-v1",
            "training_rows": len(rows),
            "complex_threshold": 0.50,
            # Only strong non-complex predictions receive the FLUX fast path.
            "primary_max_complex_probability": 0.30,
        },
        args.output,
        compress=3,
    )
    print(f"Exported tfidf-char-logreg-v1 trained on {len(rows)} prompts to {args.output}")


if __name__ == "__main__":
    main()
