"""Fixed, repeatable image prompts used to compare provider behavior."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class BenchmarkPrompt:
    id: str
    title: str
    dimension: str
    prompt: str
    reference_files: tuple[str, ...] = ()


PROMPTS = (
    BenchmarkPrompt("cinematic-scene", "Cinematic scene", "Composition & atmosphere", "A 16:9 cinematic storybook illustration of a lone explorer crossing a moonlit salt flat toward a colossal ruined observatory. A tiny warm lantern is the only orange accent. Low camera angle, distant thunderclouds, precise scale, ink and watercolor on parchment."),
    BenchmarkPrompt("character-action", "Character action", "Anatomy & action", "A children’s-book knight vaulting over a broken stone wall while protecting a small glowing fox. Full body visible, believable hands and legs, wind lifts the cape, widescreen composition, clear foreground/midground/background."),
    BenchmarkPrompt("spatial-relations", "Spatial relations", "Object binding", "A brass compass lies on a weathered map. A red feather is to the left of the compass, a glass vial is above it, and a folded blue note is below it. The objects do not overlap. Overhead 16:9 editorial photograph."),
    BenchmarkPrompt("counting", "Counting", "Counting & attributes", "A whimsical library scene containing exactly three teal owls: one perched on a ladder, one reading on a round table, and one flying near a stained-glass window. No other birds. Warm storybook ink illustration, 16:9."),
    BenchmarkPrompt("animal-handoff", "Animal handoff", "Human/animal contact", "A 16:9 editorial photograph in a sunny park. An adult park ranger wearing a tan jacket kneels on the left and offers a small red rubber ball from an open right palm. A golden retriever on the right gently takes the ball in its mouth. The ranger's left hand rests on the dog's shoulder. Show both full bodies, one ball only, natural hands, paws, and eye contact."),
    BenchmarkPrompt("dog-bicycle", "Dog and bicycle", "Animal/object interaction", "A warm 16:9 storybook illustration. A woman stands beside a blue bicycle and holds its handlebars with her left hand. Her black-and-white border collie sits beside the front wheel and carries a green canvas tote by one handle in its mouth. The bicycle remains upright; the tote does not touch the ground. Clear anatomy and believable contact points."),
    BenchmarkPrompt("horse-portrait", "Horse grooming", "People, animal & tool", "A 16:9 natural-light stable portrait. An adult rider in a navy coat stands on the horse's left side, brushing the horse's neck with a wooden grooming brush in the right hand while holding a coiled brown lead rope in the left hand. The calm chestnut horse faces right. The brush touches the neck, the rope is attached to the halter, and no extra people or horses appear."),
    BenchmarkPrompt("cat-teacup", "Cat and teacup", "Fine object interaction", "A cozy 16:9 cinematic still life with a person seated at a small round table. The person uses the right hand to lift a white teacup by its handle. A gray cat stands on a chair to the person's left and gently touches the saucer with one front paw. A teaspoon lies on the table to the right of the saucer. Keep hands, paw, cup, saucer, and spoon distinct and physically plausible."),
    BenchmarkPrompt("readable-sign", "Readable sign", "Typography", "A rainy 16:9 street scene in a colored children’s book. Above a tiny bookstore door, a hand-painted sign reads exactly: \"MOON & MOSS\". A smaller chalkboard reads exactly: \"Stories after sunset\". Keep both texts crisp and correctly spelled."),
    BenchmarkPrompt("map-card", "Map card", "Layout & labels", "A 16:9 fantasy map card, parchment and ink. A river runs from north to south, with a pine forest west of it and mountains east of it. Include exactly these labels: \"Whisperwood\", \"Silver Run\", and \"The Glass Peaks\". Elegant but legible lettering."),
    BenchmarkPrompt("reference-identity", "Character reference", "Identity preservation", "Use reference image 1 as the same knight character. Place that character on a cliff at sunrise, holding a lantern in the right hand. Preserve the helmet silhouette, armor colors, and storybook rendering; change the pose and background." , ("images/trace-knight-sword.png",)),
    BenchmarkPrompt("reference-style", "Style reference", "Style transfer", "Use reference image 1 only for its luminous electric-blue energy style. Create a quiet 16:9 scene of an ancient observatory at night, with blue energy flowing around the dome; do not copy the reference subject or composition.", ("images/trace-energy-streams.png",)),
    BenchmarkPrompt("multi-reference", "Reference composition", "Multi-image reasoning", "Use reference image 1 for the knight character and reference image 2 for the magical energy style. The knight stands in a dark forest clearing, holding a sword that emits that blue energy. Preserve the character’s identity and use the energy only on the sword and nearby mist.", ("images/trace-knight-sword.png", "images/trace-energy-streams.png")),
    BenchmarkPrompt("instruction-depth", "Instruction depth", "Prompt adherence", "A wide narrative illustration: a young astronomer in a green coat stands on the right third of a wooden bridge, looking left toward a floating city. In the foreground, two paper boats drift beneath the bridge. The city is far away and reflected in the river. Dawn light, restrained palette of indigo, moss green, and gold, colored-pencil children’s-book texture."),
)


def prompt_catalog() -> list[dict[str, object]]:
    return [{"id": item.id, "title": item.title, "dimension": item.dimension, "prompt": item.prompt, "reference_count": len(item.reference_files)} for item in PROMPTS]


def get_prompt(prompt_id: str) -> BenchmarkPrompt:
    for item in PROMPTS:
        if item.id == prompt_id:
            return item
    raise KeyError(prompt_id)
