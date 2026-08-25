"""Build a weakly-labelled image-routing corpus from DiffusionDB metadata.

Source: https://huggingface.co/datasets/poloclub/diffusiondb (CC0-1.0).
Only prompt metadata is downloaded; no images or usernames are retained.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import random
import sys
import urllib.request
from pathlib import Path

import pyarrow.parquet as pq

_CLASSIFIER_PATH = Path(__file__).resolve().parents[1] / "providers" / "local_image_prompt_classifier.py"
_classifier_spec = importlib.util.spec_from_file_location("local_image_prompt_classifier", _CLASSIFIER_PATH)
assert _classifier_spec and _classifier_spec.loader
_classifier_module = importlib.util.module_from_spec(_classifier_spec)
sys.modules[_classifier_spec.name] = _classifier_module
_classifier_spec.loader.exec_module(_classifier_module)
label_prompt = _classifier_module.HybridImageClassifier.label_prompt


DIFFUSIONDB_METADATA_URL = "https://huggingface.co/datasets/poloclub/diffusiondb/resolve/main/metadata.parquet"

def prompt_rows(metadata_path: Path, sample_size: int, seed: int):
    """Reservoir-sample safe, unique, English-like prompts without loading all rows."""
    rng = random.Random(seed)
    sample: list[str] = []
    seen: set[str] = set()
    table = pq.ParquetFile(metadata_path)
    for batch in table.iter_batches(columns=["prompt", "prompt_nsfw"], batch_size=65_536):
        prompts = batch.column("prompt").to_pylist()
        nsfw_scores = batch.column("prompt_nsfw").to_pylist()
        for prompt, nsfw_score in zip(prompts, nsfw_scores):
            if not isinstance(prompt, str) or not prompt.strip() or (nsfw_score is not None and nsfw_score > 0.05):
                continue
            normalised = " ".join(prompt.split())
            if len(normalised) < 8 or len(normalised) > 1_000 or normalised in seen:
                continue
            seen.add(normalised)
            if len(sample) < sample_size:
                sample.append(normalised)
            else:
                index = rng.randrange(len(seen))
                if index < sample_size:
                    sample[index] = normalised
    return sample


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata", type=Path, default=Path("training_data/raw/diffusiondb-metadata.parquet"))
    parser.add_argument("--output", type=Path, default=Path("training_data/diffusiondb_routing_5000.csv"))
    parser.add_argument("--sample-size", type=int, default=5_000)
    parser.add_argument("--seed", type=int, default=20260824)
    args = parser.parse_args()

    if not args.metadata.exists():
        args.metadata.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading prompt metadata from {DIFFUSIONDB_METADATA_URL}")
        urllib.request.urlretrieve(DIFFUSIONDB_METADATA_URL, args.metadata)

    prompts = prompt_rows(args.metadata, args.sample_size, args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=["id", "prompt", "multiple_characters", "creature_creature_interaction", "creature_object_interaction", "text_displayed", "complex", "label_source"],
        )
        writer.writeheader()
        for prompt in prompts:
            row = {"id": hashlib.sha256(prompt.encode()).hexdigest()[:16], "prompt": prompt, **label_prompt(prompt), "label_source": "weak_rules_v2"}
            writer.writerow(row)
    print(f"Wrote {len(prompts)} prompts to {args.output}")


if __name__ == "__main__":
    main()
