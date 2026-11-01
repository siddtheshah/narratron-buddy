"""Interactive Test Lab scenarios, evaluation, and latency benchmarking for DecisionProvider."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
import threading
import time
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

from providers.decision_provider import (
    ChoiceQuestion,
    DecisionAnswer,
    DecisionProviderError,
    DecisionQuestion,
    DecisionRequest,
    DecisionResult,
    DecisionWireAnswer,
    PredicateQuestion,
    ScoreQuestion,
)
from providers.openai_decision_provider import DEFAULT_OPENAI_DECISION_MODEL
from providers.registry import get_decision_provider, list_decision_provider_specs
from testlab.beautifier_span_benchmark import build_span_candidates, build_span_questions, rank_span_outcome

ROOT = Path(__file__).resolve().parent
router = APIRouter()

MAX_IN_FLIGHT_PER_PROVIDER = 5
_benchmark_runs: dict[str, dict[str, JsonValue]] = {}
_benchmark_lock = threading.Lock()


@dataclass(frozen=True)
class DecisionScenario:
    """A pre-authored Narratron decision scenario demonstrating how questions map to actions."""

    id: str
    title: str
    subsystem: str
    category: str
    description: str
    decision_rule: str
    expected_badge: str
    input_text: str
    questions: tuple[DecisionQuestion, ...]
    sample_wire_answers: tuple[DecisionWireAnswer, ...]

    def to_catalog_dict(self) -> dict[str, JsonValue]:
        return {
            "id": self.id,
            "title": self.title,
            "subsystem": self.subsystem,
            "category": self.category,
            "description": self.description,
            "decision_rule": self.decision_rule,
            "expected_badge": self.expected_badge,
            "input_text": self.input_text,
            "questions": [q.to_wire_dict() for q in self.questions],
        }


DECISION_SCENARIOS: tuple[DecisionScenario, ...] = (
    DecisionScenario(
        id="hybrid-image-router-complex",
        title="1. Hybrid Image Router (Complex Scene -> Gemini)",
        subsystem="providers/hybrid_image_provider.py",
        category="Image Routing",
        description=(
            "Evaluates an image prompt in one pass to decide whether FLUX.3 Klein can render it "
            "or if it must route to Gemini Image due to legible text, multi-character interactions, "
            "or complex prop manipulation."
        ),
        decision_rule=(
            "route_to_fallback = has_readable_text.passed(0.5) "
            "or has_multiple_creatures.passed(0.5) "
            "or has_object_interaction.passed(0.5) "
            "or scene_type.choice == 'complex'"
        ),
        expected_badge="COMPLEX ROUTE",
        input_text=(
            "<Aria> kneels beside a mossy stone altar while unrolling a glowing parchment scroll "
            "inscribed with the words 'ENTER THE ABYSS' as <Kael> watches from the archway."
        ),
        questions=(
            PredicateQuestion(
                name="has_readable_text",
                instructions="Does the prompt require rendering legible words, signage, or readable lettering in the image?",
            ),
            PredicateQuestion(
                name="has_multiple_creatures",
                instructions="Does the scene depict two or more distinct characters, people, or creatures?",
            ),
            PredicateQuestion(
                name="has_object_interaction",
                instructions="Is a character actively touching, holding, or manipulating a prop or object?",
            ),
            ChoiceQuestion(
                name="scene_type",
                instructions="Classify the overall visual routing complexity of the prompt.",
                choices=("pure_environment", "single_character", "complex"),
            ),
        ),
        sample_wire_answers=(
            DecisionWireAnswer(type="predicate", name="has_readable_text", probability=0.98),
            DecisionWireAnswer(type="predicate", name="has_multiple_creatures", probability=0.96),
            DecisionWireAnswer(type="predicate", name="has_object_interaction", probability=0.95),
            DecisionWireAnswer(
                type="choice",
                name="scene_type",
                choice="complex",
                probabilities={"pure_environment": 0.01, "single_character": 0.03, "complex": 0.96},
                confidence=0.96,
            ),
        ),
    ),
    DecisionScenario(
        id="hybrid-image-router-fast",
        title="2. Hybrid Image Router (Environment -> FLUX.3 Klein Fast Path)",
        subsystem="providers/hybrid_image_provider.py",
        category="Image Routing",
        description=(
            "Same 4-question routing gate evaluated on a pure environment prompt. All three complexity "
            "predicates stay well below 0.50, allowing Narratron to take the fast, $0.009 FLUX.3 Klein path."
        ),
        decision_rule=(
            "route_to_fallback = has_readable_text.passed(0.5) "
            "or has_multiple_creatures.passed(0.5) "
            "or has_object_interaction.passed(0.5) "
            "or scene_type.choice == 'complex'"
        ),
        expected_badge="FAST PATH ($0.009)",
        input_text=(
            "Wide cinematic shot of a misty sunken valley at dawn, bioluminescent mushrooms glowing "
            "along ancient basalt columns beside a still subterranean lake."
        ),
        questions=(
            PredicateQuestion(
                name="has_readable_text",
                instructions="Does the prompt require rendering legible words, signage, or readable lettering in the image?",
            ),
            PredicateQuestion(
                name="has_multiple_creatures",
                instructions="Does the scene depict two or more distinct characters, people, or creatures?",
            ),
            PredicateQuestion(
                name="has_object_interaction",
                instructions="Is a character actively touching, holding, or manipulating a prop or object?",
            ),
            ChoiceQuestion(
                name="scene_type",
                instructions="Classify the overall visual routing complexity of the prompt.",
                choices=("pure_environment", "single_character", "complex"),
            ),
        ),
        sample_wire_answers=(
            DecisionWireAnswer(type="predicate", name="has_readable_text", probability=0.01),
            DecisionWireAnswer(type="predicate", name="has_multiple_creatures", probability=0.02),
            DecisionWireAnswer(type="predicate", name="has_object_interaction", probability=0.01),
            DecisionWireAnswer(
                type="choice",
                name="scene_type",
                choice="pure_environment",
                probabilities={"pure_environment": 0.97, "single_character": 0.02, "complex": 0.01},
                confidence=0.97,
            ),
        ),
    ),
    DecisionScenario(
        id="animation-technique-planner",
        title="3. Animation Technique & Shader Effect Selector",
        subsystem="providers/animation_provider.py",
        category="Animation Planning",
        description=(
            "Selects the animation pipeline ('triframe', 'layered', or 'video') and chooses the shader "
            "motion treatments for subject and foreground layers in a single non-generative call."
        ),
        decision_rule=(
            "pipeline = technique.choice; if layered -> apply subject_effect.choice and "
            "(foreground_effect.choice if needs_foreground_layer.passed(0.5))"
        ),
        expected_badge="TECHNIQUE: LAYERED",
        input_text=(
            "An ancient iron lantern hangs from a gnarled oak branch in the midnight fog, swaying "
            "gently in the wind while pale ghostly wisps drift across the foreground."
        ),
        questions=(
            ChoiceQuestion(
                name="technique",
                instructions=(
                    "Choose 'layered' for ambient depth-separated motion, 'triframe' for 3-step cyclic "
                    "pose loops, or 'video' for full camera/character motion."
                ),
                choices=("triframe", "layered", "video"),
            ),
            ChoiceQuestion(
                name="subject_effect",
                instructions="Select the shader animation effect for the primary subject (the hanging lantern).",
                choices=("none", "sway", "vibrate", "pulse", "gentle_rocking", "light_halo", "ghostly", "energy_blast"),
            ),
            PredicateQuestion(
                name="needs_foreground_layer",
                instructions="Does the scene describe distinct foreground atmosphere (fog, wisps, rain, embers) that benefits from a separate foreground layer?",
            ),
            ChoiceQuestion(
                name="foreground_effect",
                instructions="Select the shader animation effect for the foreground atmosphere layer.",
                choices=("none", "sway", "pulse", "ghostly", "light_halo", "mirage"),
            ),
        ),
        sample_wire_answers=(
            DecisionWireAnswer(
                type="choice",
                name="technique",
                choice="layered",
                probabilities={"triframe": 0.11, "layered": 0.83, "video": 0.06},
                confidence=0.88,
            ),
            DecisionWireAnswer(
                type="choice",
                name="subject_effect",
                choice="sway",
                probabilities={
                    "none": 0.01,
                    "sway": 0.78,
                    "vibrate": 0.01,
                    "pulse": 0.04,
                    "gentle_rocking": 0.10,
                    "light_halo": 0.04,
                    "ghostly": 0.01,
                    "energy_blast": 0.01,
                },
                confidence=0.86,
            ),
            DecisionWireAnswer(type="predicate", name="needs_foreground_layer", probability=0.94),
            DecisionWireAnswer(
                type="choice",
                name="foreground_effect",
                choice="ghostly",
                probabilities={
                    "none": 0.02,
                    "sway": 0.12,
                    "pulse": 0.08,
                    "ghostly": 0.71,
                    "light_halo": 0.04,
                    "mirage": 0.03,
                },
                confidence=0.82,
            ),
        ),
    ),
    DecisionScenario(
        id="music-catalog-reranker",
        title="4. Music Catalog Candidate Reranker",
        subsystem="services/music_catalog.py",
        category="Audio & Retrieval",
        description=(
            "Evaluates TF-IDF music catalog candidates against the requested scene music prompt, "
            "picking the best candidate and scoring its musical fit on a rubric to decide whether "
            "to reuse an existing track or synthesize a new one."
        ),
        decision_rule=(
            "reuse_track = best_candidate.choice != 'none' and musical_fit.score >= 3.0 "
            "and reuse_recommended.passed(0.80)"
        ),
        expected_badge="CATALOG HIT ($0.00)",
        input_text=(
            "Requested Music Prompt: Tense subterranean stealth music with low bowed cello drones, "
            "sparse metallic percussion, and an ominous ritual pulse.\n\n"
            "Catalog Candidates:\n"
            "- track_01_tavern_lute: Warm medieval acoustic lute and flute for a lively hearthside tavern.\n"
            "- track_02_crypt_drone: Dark cavern ambience with deep cello drones, distant anvil strikes, and a slow ritual heartbeat.\n"
            "- track_03_battle_brass: Fast orchestral combat theme with driving war drums and heroic brass fanfares."
        ),
        questions=(
            ChoiceQuestion(
                name="best_candidate",
                instructions="Select the single catalog candidate ID that best serves the requested background music, or 'none' if none fit.",
                choices=("track_01_tavern_lute", "track_02_crypt_drone", "track_03_battle_brass", "none"),
            ),
            ScoreQuestion(
                name="musical_fit",
                instructions="Rate how closely the best candidate matches the requested mood, pacing, and instrumentation.",
                levels=("unrelated", "weak", "acceptable", "strong", "exact_match"),
                level_descriptions={
                    "unrelated": "Clashes in genre, mood, and tempo.",
                    "weak": "Shares only superficial tags.",
                    "acceptable": "Usable fallback mood but different instrumentation.",
                    "strong": "Matches mood, tempo, and primary instrumentation.",
                    "exact_match": "Indistinguishable fit for the requested cue.",
                },
            ),
            PredicateQuestion(
                name="reuse_recommended",
                instructions="Does the best candidate fit well enough to play immediately instead of paying to generate a new track?",
            ),
        ),
        sample_wire_answers=(
            DecisionWireAnswer(
                type="choice",
                name="best_candidate",
                choice="track_02_crypt_drone",
                probabilities={
                    "track_01_tavern_lute": 0.01,
                    "track_02_crypt_drone": 0.95,
                    "track_03_battle_brass": 0.02,
                    "none": 0.02,
                },
                confidence=0.95,
            ),
            DecisionWireAnswer(
                type="score",
                name="musical_fit",
                score=3.48,
                probabilities={
                    "unrelated": 0.00,
                    "weak": 0.01,
                    "acceptable": 0.06,
                    "strong": 0.37,
                    "exact_match": 0.56,
                },
                confidence=0.92,
            ),
            DecisionWireAnswer(type="predicate", name="reuse_recommended", probability=0.94),
        ),
    ),
    DecisionScenario(
        id="reference-tag-and-voice-casting",
        title="5. Reference <Tag> Classification & Voice Casting",
        subsystem="components/reference_manager.py",
        category="Character & Lore State",
        description=(
            "Classifies unknown <Tag> references into 'character' vs 'scene' and casts vocal attributes "
            "and character quirks in one batch call when new entities appear."
        ),
        decision_rule=(
            "Register <ObsidianSanctum> as tag_ObsidianSanctum.choice; register <LadyVespera> as "
            "tag_LadyVespera.choice with voice=(gender, pitch, persona) and quirk"
        ),
        expected_badge="REFERENCES CAST",
        input_text=(
            "Inside the <ObsidianSanctum>, <LadyVespera>, an elderly high inquisitor with a raspy "
            "aristocratic voice and icy composure, raises her staff to seal the vault."
        ),
        questions=(
            ChoiceQuestion(
                name="tag_ObsidianSanctum",
                instructions="Classify whether <ObsidianSanctum> refers to a character/creature or a location/scene.",
                choices=("character", "scene"),
            ),
            ChoiceQuestion(
                name="tag_LadyVespera",
                instructions="Classify whether <LadyVespera> refers to a character/creature or a location/scene.",
                choices=("character", "scene"),
            ),
            ChoiceQuestion(
                name="vespera_gender",
                instructions="Select <LadyVespera>'s vocal gender presentation for speech synthesis.",
                choices=("female", "male", "nonbinary"),
            ),
            ChoiceQuestion(
                name="vespera_pitch",
                instructions="Select the best vocal pitch register for <LadyVespera>.",
                choices=("low", "medium", "high"),
            ),
            ChoiceQuestion(
                name="vespera_quirk",
                instructions="Choose the most fitting roleplay quirk for <LadyVespera> from the available pool.",
                choices=(
                    "Speaks in hushed, measured cadences",
                    "Constantly clears throat nervously",
                    "Laughs boisterously at inappropriate moments",
                    "Clips every sentence with icy precision",
                ),
            ),
        ),
        sample_wire_answers=(
            DecisionWireAnswer(
                type="choice",
                name="tag_ObsidianSanctum",
                choice="scene",
                probabilities={"character": 0.02, "scene": 0.98},
                confidence=0.98,
            ),
            DecisionWireAnswer(
                type="choice",
                name="tag_LadyVespera",
                choice="character",
                probabilities={"character": 0.99, "scene": 0.01},
                confidence=0.99,
            ),
            DecisionWireAnswer(
                type="choice",
                name="vespera_gender",
                choice="female",
                probabilities={"female": 0.97, "male": 0.01, "nonbinary": 0.02},
                confidence=0.97,
            ),
            DecisionWireAnswer(
                type="choice",
                name="vespera_pitch",
                choice="low",
                probabilities={"low": 0.79, "medium": 0.18, "high": 0.03},
                confidence=0.84,
            ),
            DecisionWireAnswer(
                type="choice",
                name="vespera_quirk",
                choice="Clips every sentence with icy precision",
                probabilities={
                    "Speaks in hushed, measured cadences": 0.21,
                    "Constantly clears throat nervously": 0.02,
                    "Laughs boisterously at inappropriate moments": 0.01,
                    "Clips every sentence with icy precision": 0.76,
                },
                confidence=0.83,
            ),
        ),
    ),
    DecisionScenario(
        id="game-move-narration-gate",
        title="6. Board/Card Game Move Narration Gate",
        subsystem="components/story/story_recorder_module.py",
        category="Story & Game Gating",
        description=(
            "Evaluates a committed player board-game move to decide in ~100ms whether the move is "
            "dramatic enough to wake up the StoryResponderModule for in-character commentary."
        ),
        decision_rule=(
            "trigger_narration = should_narrate_move.passed(0.60) or drama_level.score >= 2.0"
        ),
        expected_badge="NARRATION TRIGGERED",
        input_text=(
            "Game: Checkers. Turn #14. Player jumps two of Narratron's kings in a double-capture, "
            "crowning their final piece on the back rank and taking a 3-to-1 material lead."
        ),
        questions=(
            PredicateQuestion(
                name="should_narrate_move",
                instructions="Is this game move a dramatic capture, blunder, kinging, or momentum swing worth immediate in-character narration?",
            ),
            ScoreQuestion(
                name="drama_level",
                instructions="Rate the narrative significance of this board-game move.",
                levels=("routine", "notable", "turning_point", "climactic"),
            ),
            ChoiceQuestion(
                name="narrator_reaction_tone",
                instructions="How should Narratron react in character to this player move?",
                choices=("unbothered", "impressed", "rattled", "triumphant"),
            ),
        ),
        sample_wire_answers=(
            DecisionWireAnswer(type="predicate", name="should_narrate_move", probability=0.96),
            DecisionWireAnswer(
                type="score",
                name="drama_level",
                score=2.42,
                probabilities={
                    "routine": 0.01,
                    "notable": 0.08,
                    "turning_point": 0.39,
                    "climactic": 0.52,
                },
                confidence=0.90,
            ),
            DecisionWireAnswer(
                type="choice",
                name="narrator_reaction_tone",
                choice="rattled",
                probabilities={
                    "unbothered": 0.04,
                    "impressed": 0.31,
                    "rattled": 0.62,
                    "triumphant": 0.03,
                },
                confidence=0.79,
            ),
        ),
    ),
    DecisionScenario(
        id="text-beautifier-span-selector",
        title="7. Scene Emotive Text Span Selector (De-Textified Beautifier)",
        subsystem="services/text_beautifier.py",
        category="UI & Typography",
        description=(
            "Pre-splits scene narration into candidate spans in Python and uses the Decision API to "
            "pick which span gets a kinetic Splitting.js animation and display font—avoiding slow "
            "autoregressive re-emission of the entire scene text."
        ),
        decision_rule=(
            "Apply kinetic_effect.choice + display_font.choice to highlight_span.choice"
        ),
        expected_badge="SPAN: SPAN_2",
        input_text=(
            "Candidate Narration Spans:\n"
            "- span_1: \"The cavern floor fractures with a\"\n"
            "- span_2: \"DEAFENING CRACK\"\n"
            "- span_3: \"as an ancient dragon erupts from the molten chasm!\""
        ),
        questions=(
            ChoiceQuestion(
                name="highlight_span",
                instructions="Which candidate span carries the peak kinetic/emotional impact and should receive visual text styling?",
                choices=("span_1", "span_2", "span_3", "none"),
            ),
            ChoiceQuestion(
                name="kinetic_effect",
                instructions="Select the kinetic text animation effect for the highlighted span.",
                choices=("none", "vibrate", "glitch", "flame", "pulse", "glow", "wave", "drip"),
            ),
            ChoiceQuestion(
                name="display_font",
                instructions="Select the display font family for the highlighted span.",
                choices=("default", "cinematic", "lacquer", "bangers", "medieval", "glitch"),
            ),
        ),
        sample_wire_answers=(
            DecisionWireAnswer(
                type="choice",
                name="highlight_span",
                choice="span_2",
                probabilities={"span_1": 0.02, "span_2": 0.84, "span_3": 0.13, "none": 0.01},
                confidence=0.89,
            ),
            DecisionWireAnswer(
                type="choice",
                name="kinetic_effect",
                choice="vibrate",
                probabilities={
                    "none": 0.01,
                    "vibrate": 0.79,
                    "glitch": 0.03,
                    "flame": 0.11,
                    "pulse": 0.04,
                    "glow": 0.01,
                    "wave": 0.00,
                    "drip": 0.01,
                },
                confidence=0.87,
            ),
            DecisionWireAnswer(
                type="choice",
                name="display_font",
                choice="bangers",
                probabilities={
                    "default": 0.02,
                    "cinematic": 0.11,
                    "lacquer": 0.05,
                    "bangers": 0.76,
                    "medieval": 0.04,
                    "glitch": 0.02,
                },
                confidence=0.85,
            ),
        ),
    ),
)


# Keep the original hand-authored span-choice example for comparison.
SPAN_BENCHMARK_CASES: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "beautifier-ranked-short", "8. Ranked Emphasis: Two Short Highlights",
        "The cavern splits with a DEAFENING CRACK as dust falls.\n"
        "Beyond the gate, RUN NOW echoes through the dark.",
        ("DEAFENING CRACK", "RUN NOW"),
    ),
    (
        "beautifier-ranked-line", "9. Ranked Emphasis: Whole Line",
        "The guard reads the inscription.\nYOU SHALL NOT PASS!",
        ("YOU SHALL NOT PASS!",),
    ),
    (
        "beautifier-ranked-quiet", "10. Ranked Emphasis: Quiet Narration",
        "Morning light rests on the table.\nA cup stands beside the folded cloth.",
        (),
    ),
)


def _build_span_benchmark_scenario(
    scenario_id: str, title: str, text: str, expected: tuple[str, ...],
) -> DecisionScenario:
    return DecisionScenario(
        id=scenario_id, title=title, subsystem="services/text_beautifier.py",
        category="UI & Typography",
        description=(
            "Generate every one-word, adjacent two-word, and whole-line candidate from source text. "
            "Evaluate independent emphasis suitability predicates in one request, then rank their "
            "probabilities and select up to two nonoverlapping highlights above 60%. "
            "Questions regenerate from edited text in live mode. Walkthrough probabilities are "
            "illustrative. Agreement measures exact reference spans; alternative valid highlights may differ."
        ),
        decision_rule="Sort by probability; accept >= 0.60; skip overlaps; stop after two spans.",
        expected_badge="SPANS: " + (" | ".join(expected) or "NONE"),
        input_text=text,
        questions=build_span_questions(text),
        sample_wire_answers=tuple(DecisionWireAnswer(
            type="predicate", name=candidate.name,
            probability=0.96 if candidate.text in expected else 0.12,
        ) for candidate in build_span_candidates(text)),
    )


DECISION_SCENARIOS += tuple(
    _build_span_benchmark_scenario(*case) for case in SPAN_BENCHMARK_CASES
)


class DecisionQuestionSpec(BaseModel):
    """JSON request schema for a single question submitted from the Test Lab UI."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["predicate", "choice", "score"]
    name: str
    instructions: str
    choices: list[str] = Field(default_factory=list)
    levels: list[str] = Field(default_factory=list)
    level_descriptions: dict[str, str] | None = None

    def to_question(self) -> DecisionQuestion:
        if self.type == "predicate":
            return PredicateQuestion(name=self.name, instructions=self.instructions)
        if self.type == "choice":
            return ChoiceQuestion(
                name=self.name,
                instructions=self.instructions,
                choices=self.choices,
            )
        return ScoreQuestion(
            name=self.name,
            instructions=self.instructions,
            levels=self.levels,
            level_descriptions=self.level_descriptions,
        )

    def build_fallback_wire_answer(self) -> DecisionWireAnswer:
        """Construct a deterministic walkthrough answer when custom questions are run in walkthrough mode."""
        if self.type == "predicate":
            return DecisionWireAnswer(type="predicate", name=self.name, probability=0.82)
        if self.type == "choice":
            chosen = self.choices[0]
            count = len(self.choices)
            top_prob = 0.76
            rem = round((1.0 - top_prob) / max(1, count - 1), 4)
            probs = {
                opt: (top_prob if idx == 0 else rem)
                for idx, opt in enumerate(self.choices)
            }
            return DecisionWireAnswer(
                type="choice",
                name=self.name,
                choice=chosen,
                probabilities=probs,
                confidence=0.84,
            )
        top_idx = len(self.levels) - 1
        probs = {
            lvl: (0.70 if idx == top_idx else round(0.30 / max(1, len(self.levels) - 1), 4))
            for idx, lvl in enumerate(self.levels)
        }
        weighted = sum(idx * probs[lvl] for idx, lvl in enumerate(self.levels))
        return DecisionWireAnswer(
            type="score",
            name=self.name,
            score=round(weighted, 2),
            probabilities=probs,
            confidence=0.85,
        )


class DecisionLabEvaluateRequest(BaseModel):
    """Payload posted to /api/decision-lab/evaluate."""

    model_config = ConfigDict(extra="forbid")

    provider_id: str = "openai-decisions"
    model: str | None = None
    mode: Literal["live", "walkthrough"] = "walkthrough"
    scenario_id: str | None = None
    input_text: str | None = None
    questions: list[DecisionQuestionSpec] | None = None


class DecisionBenchmarkRunRequest(BaseModel):
    """Payload posted to /api/decision-lab/benchmark/runs."""

    model_config = ConfigDict(extra="forbid")

    provider_ids: list[str]
    scenario_ids: list[str]
    repetitions: int = Field(default=3, ge=1, le=20)
    provider_options: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    synchronous: bool = False


def decision_scenario_catalog() -> list[dict[str, JsonValue]]:
    return [scenario.to_catalog_dict() for scenario in DECISION_SCENARIOS]


def get_decision_scenario(scenario_id: str) -> DecisionScenario:
    for scenario in DECISION_SCENARIOS:
        if scenario.id == scenario_id:
            return scenario
    raise KeyError(scenario_id)


def synthesize_narratron_outcome(
    scenario_id: str | None,
    result: DecisionResult,
    input_text: str | None = None,
) -> dict[str, JsonValue]:
    """Translate raw decision answers into the concrete runtime action Narratron takes."""
    if scenario_id and scenario_id.startswith("beautifier-ranked-"):
        text = input_text if input_text is not None else get_decision_scenario(scenario_id).input_text
        return rank_span_outcome(text, result)
    answers = result.answers

    if scenario_id in ("hybrid-image-router-complex", "hybrid-image-router-fast") and {
        "has_readable_text",
        "has_multiple_creatures",
        "has_object_interaction",
        "scene_type",
    }.issubset(answers.keys()):
        text_pred = result.get_predicate("has_readable_text")
        multi_pred = result.get_predicate("has_multiple_creatures")
        obj_pred = result.get_predicate("has_object_interaction")
        scene_choice = result.get_choice("scene_type")

        reasons: list[str] = []
        if text_pred.passed(0.5):
            reasons.append(f"has_readable_text={text_pred.probability:.0%} (>= 50%)")
        if multi_pred.passed(0.5):
            reasons.append(f"has_multiple_creatures={multi_pred.probability:.0%} (>= 50%)")
        if obj_pred.passed(0.5):
            reasons.append(f"has_object_interaction={obj_pred.probability:.0%} (>= 50%)")
        if scene_choice.choice == "complex":
            reasons.append("scene_type='complex'")

        route_to_fallback = len(reasons) > 0
        if route_to_fallback:
            return {
                "headline": "Route -> Gemini 3.1 Flash-Lite Image (Fallback)",
                "badge": "COMPLEX ROUTE",
                "status": "fallback",
                "explanation": (
                    "Prompt triggered complexity guards ("
                    + ", ".join(reasons)
                    + "); routed to Gemini Image for high-precision multi-subject/text rendering."
                ),
                "trace": [
                    f"has_readable_text: {text_pred.probability:.0%} -> {'TRIGGERED' if text_pred.passed(0.5) else 'clear'}",
                    f"has_multiple_creatures: {multi_pred.probability:.0%} -> {'TRIGGERED' if multi_pred.passed(0.5) else 'clear'}",
                    f"has_object_interaction: {obj_pred.probability:.0%} -> {'TRIGGERED' if obj_pred.passed(0.5) else 'clear'}",
                    f"scene_type: '{scene_choice.choice}' ({scene_choice.probabilities.get(scene_choice.choice, 0.0):.0%})",
                ],
            }
        return {
            "headline": f"Route -> FLUX.3 Klein Fast Path ({scene_choice.choice})",
            "badge": "FAST PATH ($0.009)",
            "status": "fast",
            "explanation": (
                "All complexity predicates scored below 50% and scene_type is "
                f"'{scene_choice.choice}'; safe to render on FLUX.3 Klein."
            ),
            "trace": [
                f"has_readable_text: {text_pred.probability:.0%} (< 50%)",
                f"has_multiple_creatures: {multi_pred.probability:.0%} (< 50%)",
                f"has_object_interaction: {obj_pred.probability:.0%} (< 50%)",
                f"scene_type: '{scene_choice.choice}'",
            ],
        }

    if scenario_id == "animation-technique-planner" and {
        "technique",
        "subject_effect",
        "needs_foreground_layer",
        "foreground_effect",
    }.issubset(answers.keys()):
        tech = result.get_choice("technique")
        subj = result.get_choice("subject_effect")
        needs_fg = result.get_predicate("needs_foreground_layer")
        fg = result.get_choice("foreground_effect")
        fg_summary = f", foreground='{fg.choice}'" if needs_fg.passed(0.5) else " (no foreground layer)"
        return {
            "headline": f"Pipeline -> {tech.choice.upper()} Animation (subject='{subj.choice}'{fg_summary})",
            "badge": f"TECHNIQUE: {tech.choice.upper()}",
            "status": "action",
            "explanation": (
                f"Selected '{tech.choice}' animation technique with subject shader '{subj.choice}'"
                + (
                    f" and foreground layer shader '{fg.choice}' (needs_foreground_layer={needs_fg.probability:.0%})."
                    if needs_fg.passed(0.5)
                    else f" (foreground layer skipped at {needs_fg.probability:.0%})."
                )
            ),
            "trace": [
                f"technique: '{tech.choice}' (confidence {tech.confidence or 0.0:.0%})",
                f"subject_effect: '{subj.choice}'",
                f"needs_foreground_layer: {needs_fg.probability:.0%} -> {'INCLUDE' if needs_fg.passed(0.5) else 'OMIT'}",
                f"foreground_effect: '{fg.choice}'",
            ],
        }

    if scenario_id == "music-catalog-reranker" and {
        "best_candidate",
        "musical_fit",
        "reuse_recommended",
    }.issubset(answers.keys()):
        cand = result.get_choice("best_candidate")
        fit = result.get_score("musical_fit")
        reuse = result.get_predicate("reuse_recommended")
        should_reuse = cand.choice != "none" and fit.score >= 3.0 and reuse.passed(0.80)
        if should_reuse:
            return {
                "headline": f"Reuse Catalog Track -> {cand.choice} (fit={fit.score:.2f}/4.0)",
                "badge": "CATALOG HIT ($0.00)",
                "status": "fast",
                "explanation": (
                    f"Candidate '{cand.choice}' passed semantic reranking with rubric score {fit.score:.2f}/4.0 "
                    f"and reuse probability {reuse.probability:.0%}; skipping new music generation."
                ),
                "trace": [
                    f"best_candidate: '{cand.choice}' ({cand.probabilities.get(cand.choice, 0.0):.0%})",
                    f"musical_fit: {fit.score:.2f} / 4.0 (>= 3.0 threshold)",
                    f"reuse_recommended: {reuse.probability:.0%} (>= 80% threshold)",
                ],
            }
        return {
            "headline": "Synthesize New Track via MusicProvider",
            "badge": "GENERATE NEW",
            "status": "fallback",
            "explanation": (
                f"Top candidate '{cand.choice}' scored {fit.score:.2f}/4.0 "
                f"(reuse={reuse.probability:.0%}), below the catalog reuse threshold."
            ),
            "trace": [
                f"best_candidate: '{cand.choice}'",
                f"musical_fit: {fit.score:.2f} / 4.0",
                f"reuse_recommended: {reuse.probability:.0%}",
            ],
        }

    if scenario_id == "reference-tag-and-voice-casting" and {
        "tag_ObsidianSanctum",
        "tag_LadyVespera",
        "vespera_gender",
        "vespera_pitch",
        "vespera_quirk",
    }.issubset(answers.keys()):
        sanctum = result.get_choice("tag_ObsidianSanctum")
        vespera = result.get_choice("tag_LadyVespera")
        gender = result.get_choice("vespera_gender")
        pitch = result.get_choice("vespera_pitch")
        quirk = result.get_choice("vespera_quirk")
        return {
            "headline": (
                f"Index <ObsidianSanctum> as {sanctum.choice.upper()} & "
                f"<LadyVespera> as {vespera.choice.upper()} ({gender.choice}, {pitch.choice} pitch)"
            ),
            "badge": "REFERENCES CAST",
            "status": "action",
            "explanation": (
                f"Created scene reference <ObsidianSanctum> and character <LadyVespera> with "
                f"voice_tags=['gender={gender.choice}', 'pitch={pitch.choice}'] and quirk='{quirk.choice}'."
            ),
            "trace": [
                f"<ObsidianSanctum> -> '{sanctum.choice}' ({sanctum.probabilities.get(sanctum.choice, 0.0):.0%})",
                f"<LadyVespera> -> '{vespera.choice}' ({vespera.probabilities.get(vespera.choice, 0.0):.0%})",
                f"Voice tags -> gender={gender.choice}, pitch={pitch.choice}",
                f"Assigned quirk -> '{quirk.choice}'",
            ],
        }

    if scenario_id == "game-move-narration-gate" and {
        "should_narrate_move",
        "drama_level",
        "narrator_reaction_tone",
    }.issubset(answers.keys()):
        narrate = result.get_predicate("should_narrate_move")
        drama = result.get_score("drama_level")
        tone = result.get_choice("narrator_reaction_tone")
        wake_responder = narrate.passed(0.60) or drama.score >= 2.0
        if wake_responder:
            return {
                "headline": f"Wake StoryResponderModule (Tone: {tone.choice.upper()})",
                "badge": "NARRATION TRIGGERED",
                "status": "action",
                "explanation": (
                    f"Move exceeded drama threshold (should_narrate_move={narrate.probability:.0%}, "
                    f"drama_level={drama.score:.2f}/3.0). Queuing '{tone.choice}' in-character reaction."
                ),
                "trace": [
                    f"should_narrate_move: {narrate.probability:.0%} (>= 60%)",
                    f"drama_level: {drama.score:.2f} / 3.0",
                    f"narrator_reaction_tone: '{tone.choice}'",
                ],
            }
        return {
            "headline": "Silent Board Update (Skip Story Turn)",
            "badge": "QUIET MOVE",
            "status": "skip",
            "explanation": "Routine move below narration threshold; board state updated without interrupting play.",
            "trace": [
                f"should_narrate_move: {narrate.probability:.0%} (< 60%)",
                f"drama_level: {drama.score:.2f} / 3.0",
            ],
        }

    if scenario_id == "text-beautifier-span-selector" and {
        "highlight_span",
        "kinetic_effect",
        "display_font",
    }.issubset(answers.keys()):
        span = result.get_choice("highlight_span")
        eff = result.get_choice("kinetic_effect")
        font = result.get_choice("display_font")
        return {
            "headline": f"Style {span.choice} -> effect='{eff.choice}', font='{font.choice}'",
            "badge": f"SPAN: {span.choice.upper()}",
            "status": "action",
            "explanation": (
                f"Selected '{span.choice}' for kinetic styling with Splitting.js effect '{eff.choice}' "
                f"and font '{font.choice}' without re-generating scene prose."
            ),
            "trace": [
                f"highlight_span: '{span.choice}' ({span.probabilities.get(span.choice, 0.0):.0%})",
                f"kinetic_effect: '{eff.choice}'",
                f"display_font: '{font.choice}'",
            ],
        }

    generic_trace: list[JsonValue] = []
    for name, ans in answers.items():
        serialized = ans.to_dict()
        if ans.answer_type == "predicate":
            generic_trace.append(
                f"{name} (predicate): {float(serialized.get('probability') or 0.0):.0%} "
                f"({'PASSED' if serialized.get('passed') else 'FALSE'})"
            )
        elif ans.answer_type == "choice":
            generic_trace.append(f"{name} (choice): '{serialized.get('choice')}'")
        else:
            generic_trace.append(f"{name} (score): {serialized.get('score')}")

    return {
        "headline": f"Evaluated {len(answers)} Decision Question{'s' if len(answers) != 1 else ''}",
        "badge": "CUSTOM DECISION",
        "status": "action",
        "explanation": "All structured questions evaluated in a single pass against shared input evidence.",
        "trace": generic_trace,
    }


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = (len(sorted_values) - 1) * fraction
    lower = int(index)
    upper = min(len(sorted_values) - 1, lower + 1)
    weight = index - lower
    return round(sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * weight, 2)


def _extract_numeric_token_count(usage: dict[str, JsonValue], keys: tuple[str, ...]) -> float:
    for key in keys:
        if key in usage and usage[key] is not None:
            try:
                return TypeAdapter(float).validate_python(usage[key])
            except ValidationError:
                continue
    return 0.0


def compute_benchmark_summary(
    provider_ids: list[str],
    scenarios: list[DecisionScenario],
    items: list[dict[str, JsonValue]],
) -> dict[str, JsonValue]:
    """Compute latency percentiles, speedup multipliers, token counts, and outcome agreement."""
    expected_by_scenario = {sc.id: sc.expected_badge for sc in scenarios}
    provider_stats: dict[str, dict[str, JsonValue]] = {}
    mean_by_provider: dict[str, float] = {}

    for provider_id in provider_ids:
        prov_items = [item for item in items if item.get("provider_id") == provider_id]
        done_items = [item for item in prov_items if item.get("status") == "completed"]
        latencies = sorted(
            TypeAdapter(float).validate_python(item["latency_ms"])
            for item in done_items
            if "latency_ms" in item
        )

        matches = 0
        input_tokens_list: list[float] = []
        output_tokens_list: list[float] = []
        for item in done_items:
            sc_id = str(item.get("scenario_id") or "")
            badge = str(item.get("outcome_badge") or "")
            if sc_id in expected_by_scenario and badge == expected_by_scenario[sc_id]:
                matches += 1
            usage_raw = item.get("usage")
            try:
                usage_map = TypeAdapter(dict[str, JsonValue]).validate_python(usage_raw or {})
            except ValidationError:
                usage_map = {}
            input_tokens_list.append(
                _extract_numeric_token_count(usage_map, ("input_tokens", "prompt_token_count"))
            )
            output_tokens_list.append(
                _extract_numeric_token_count(usage_map, ("output_tokens", "candidates_token_count"))
            )

        mean_ms = round(sum(latencies) / len(latencies), 2) if latencies else None
        if mean_ms is not None and mean_ms > 0:
            mean_by_provider[provider_id] = mean_ms

        provider_stats[provider_id] = {
            "provider_id": provider_id,
            "total": len(prov_items),
            "completed": len(done_items),
            "failed": len(prov_items) - len(done_items),
            "mean_ms": mean_ms,
            "p50_ms": _percentile(latencies, 0.50) if latencies else None,
            "p95_ms": _percentile(latencies, 0.95) if latencies else None,
            "min_ms": round(latencies[0], 2) if latencies else None,
            "max_ms": round(latencies[-1], 2) if latencies else None,
            "mean_input_tokens": (
                round(sum(input_tokens_list) / len(input_tokens_list), 1)
                if input_tokens_list
                else 0.0
            ),
            "mean_output_tokens": (
                round(sum(output_tokens_list) / len(output_tokens_list), 1)
                if output_tokens_list
                else 0.0
            ),
            "outcome_match_rate": (
                round(matches / len(done_items), 4) if done_items else None
            ),
            "speedup_vs_slowest": None,
        }

    slowest_mean = max(mean_by_provider.values()) if mean_by_provider else None
    if slowest_mean is not None and slowest_mean > 0:
        for provider_id, mean_val in mean_by_provider.items():
            provider_stats[provider_id]["speedup_vs_slowest"] = round(
                slowest_mean / mean_val, 2
            )

    by_scenario: dict[str, JsonValue] = {}
    for scenario in scenarios:
        sc_providers: dict[str, JsonValue] = {}
        sc_means: dict[str, float] = {}
        for provider_id in provider_ids:
            cell_items = [
                item
                for item in items
                if item.get("scenario_id") == scenario.id
                and item.get("provider_id") == provider_id
                and item.get("status") == "completed"
            ]
            cell_latencies = sorted(
                TypeAdapter(float).validate_python(item["latency_ms"])
                for item in cell_items
                if "latency_ms" in item
            )
            cell_mean = (
                round(sum(cell_latencies) / len(cell_latencies), 2)
                if cell_latencies
                else None
            )
            if cell_mean is not None and cell_mean > 0:
                sc_means[provider_id] = cell_mean
            latest_badge = str(cell_items[-1].get("outcome_badge") or "") if cell_items else None
            latest_headline = str(cell_items[-1].get("outcome_headline") or "") if cell_items else None
            sc_providers[provider_id] = {
                "completed": len(cell_items),
                "mean_ms": cell_mean,
                "p50_ms": _percentile(cell_latencies, 0.50) if cell_latencies else None,
                "min_ms": round(cell_latencies[0], 2) if cell_latencies else None,
                "max_ms": round(cell_latencies[-1], 2) if cell_latencies else None,
                "outcome_badge": latest_badge,
                "outcome_headline": latest_headline,
                "matches_expected": latest_badge == scenario.expected_badge if latest_badge else None,
                "speedup_vs_slowest": None,
            }
        sc_slowest = max(sc_means.values()) if sc_means else None
        if sc_slowest is not None and sc_slowest > 0:
            for provider_id, cell_mean in sc_means.items():
                cell_dict = TypeAdapter(dict[str, JsonValue]).validate_python(sc_providers[provider_id])
                cell_dict["speedup_vs_slowest"] = round(sc_slowest / cell_mean, 2)
                sc_providers[provider_id] = cell_dict
        by_scenario[scenario.id] = {
            "scenario_id": scenario.id,
            "title": scenario.title,
            "expected_badge": scenario.expected_badge,
            "providers": sc_providers,
        }

    return {
        "providers": provider_stats,
        "by_scenario": by_scenario,
    }


def _benchmark_one_decision(
    provider_id: str,
    scenario: DecisionScenario,
    repetition: int,
    options: dict[str, JsonValue],
    semaphore: threading.BoundedSemaphore,
) -> dict[str, JsonValue]:
    with semaphore:
        started = time.perf_counter()
        item: dict[str, JsonValue] = {
            "id": uuid.uuid4().hex,
            "provider_id": provider_id,
            "scenario_id": scenario.id,
            "scenario_title": scenario.title,
            "repetition": repetition,
            "started_at": time.time(),
            "status": "failed",
        }
        try:
            provider = get_decision_provider(provider_id, options=options)
            model_override = str(options["model"]) if "model" in options and options["model"] is not None else None
            req = DecisionRequest(
                input_text=scenario.input_text,
                questions=scenario.questions,
                model=model_override,
            )
            result = provider.decide(req)
            outcome = synthesize_narratron_outcome(scenario.id, result, req.input_text)
            item.update(
                {
                    "status": "completed",
                    "model": result.model,
                    "request_id": result.request_id,
                    "usage": dict(result.usage),
                    "answers": [result.get_answer(q.name).to_dict() for q in scenario.questions],
                    "narratron_outcome": outcome,
                    "outcome_badge": outcome.get("badge"),
                    "outcome_headline": outcome.get("headline"),
                }
            )
        except Exception as exc:
            item["error"] = str(exc)
        finally:
            item["completed_at"] = time.time()
            item["latency_ms"] = round((time.perf_counter() - started) * 1000.0, 2)
        return item


def _run_decision_benchmark(
    run_id: str,
    provider_ids: list[str],
    scenarios: list[DecisionScenario],
    repetitions: int,
    provider_options: dict[str, dict[str, JsonValue]],
) -> None:
    task_count = len(provider_ids) * len(scenarios) * repetitions
    worker_count = max(1, min(task_count, MAX_IN_FLIGHT_PER_PROVIDER * len(provider_ids)))
    semaphores = {
        pid: threading.BoundedSemaphore(MAX_IN_FLIGHT_PER_PROVIDER)
        for pid in provider_ids
    }
    collected_items: list[dict[str, JsonValue]] = []
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="decision-bench") as executor:
        futures = [
            executor.submit(
                _benchmark_one_decision,
                provider_id,
                scenario,
                repetition,
                dict(provider_options.get(provider_id) or {}),
                semaphores[provider_id],
            )
            for repetition in range(1, repetitions + 1)
            for scenario in scenarios
            for provider_id in provider_ids
        ]
        for future in as_completed(futures):
            item = future.result()
            collected_items.append(item)
            with _benchmark_lock:
                run = _benchmark_runs.get(run_id)
                if run is not None:
                    current_items = TypeAdapter(list[dict[str, JsonValue]]).validate_python(run["items"])
                    current_items.append(item)
                    run["items"] = current_items
                    run["summary"] = compute_benchmark_summary(provider_ids, scenarios, current_items)

    with _benchmark_lock:
        run = _benchmark_runs.get(run_id)
        if run is not None:
            run["status"] = "completed"
            run["completed_at"] = time.time()
            run["summary"] = compute_benchmark_summary(provider_ids, scenarios, collected_items)


@router.get("/decision-lab", include_in_schema=False)
@router.get("/decision-benchmark", include_in_schema=False)
def decision_lab_page() -> FileResponse:
    return FileResponse(ROOT / "decision_lab.html", media_type="text/html")


@router.get("/api/decision-lab/catalog")
def decision_lab_catalog() -> dict[str, JsonValue]:
    return {
        "providers": list_decision_provider_specs(),
        "scenarios": decision_scenario_catalog(),
    }


@router.post("/api/decision-lab/evaluate")
def evaluate_decision_lab(body: DecisionLabEvaluateRequest) -> dict[str, JsonValue]:
    scenario: DecisionScenario | None = None
    if body.scenario_id:
        try:
            scenario = get_decision_scenario(body.scenario_id)
        except KeyError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown decision scenario: {body.scenario_id}",
            ) from exc

    input_text = (body.input_text if body.input_text is not None else (scenario.input_text if scenario else "")).strip()
    if not input_text:
        raise HTTPException(status_code=400, detail="input_text cannot be empty.")

    try:
        if body.scenario_id and body.scenario_id.startswith("beautifier-ranked-"):
            questions = build_span_questions(input_text)
            if body.mode == "walkthrough" and scenario is not None and input_text != scenario.input_text:
                raise HTTPException(status_code=400, detail="Use live mode to score edited narration; walkthrough samples use the original text.")
        elif body.questions is not None:
            questions: tuple[DecisionQuestion, ...] = tuple(spec.to_question() for spec in body.questions)
        elif scenario is not None:
            questions = scenario.questions
        else:
            raise HTTPException(status_code=400, detail="Provide at least one question or a scenario_id.")

        decision_request = DecisionRequest(
            input_text=input_text,
            questions=questions,
            model=body.model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    target_model = (body.model or DEFAULT_OPENAI_DECISION_MODEL).strip()
    request_wire: dict[str, JsonValue] = {
        "model": target_model,
        "input": decision_request.input_text,
        "questions": [q.to_wire_dict() for q in decision_request.questions],
    }

    start = time.perf_counter()
    if body.mode == "walkthrough":
        sample_by_name: dict[str, DecisionWireAnswer] = (
            {ans.name: ans for ans in scenario.sample_wire_answers}
            if scenario is not None
            else {}
        )
        spec_by_name: dict[str, DecisionQuestionSpec] = (
            {spec.name: spec for spec in body.questions}
            if body.questions is not None
            else {}
        )
        parsed_answers: dict[str, DecisionAnswer] = {}
        for question in decision_request.questions:
            wire_ans = sample_by_name.get(question.name)
            if wire_ans is None and question.name in spec_by_name:
                wire_ans = spec_by_name[question.name].build_fallback_wire_answer()
            if wire_ans is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"No walkthrough sample answer configured for '{question.name}'.",
                )
            try:
                parsed_answers[question.name] = question.parse_wire_answer(wire_ans)
            except DecisionProviderError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

        result = DecisionResult(
            provider="walkthrough-preview",
            model=target_model,
            answers=parsed_answers,
            request_id="walkthrough_sample",
            usage={"input_tokens": max(24, len(input_text.split()) * 2), "output_tokens": 0},
        )
    else:
        try:
            options: dict[str, JsonValue] = {"model": target_model} if body.model else {}
            provider = get_decision_provider(body.provider_id, options=options)
            result = provider.decide(decision_request)
        except DecisionProviderError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    latency_ms = round((time.perf_counter() - start) * 1000.0, 2)
    serialized_answers: list[JsonValue] = [
        result.get_answer(q.name).to_dict() for q in decision_request.questions
    ]

    return {
        "mode": body.mode,
        "provider": result.provider,
        "model": result.model,
        "request_id": result.request_id,
        "latency_ms": latency_ms,
        "request_wire": request_wire,
        "answers": serialized_answers,
        "narratron_outcome": synthesize_narratron_outcome(body.scenario_id, result, input_text),
        "usage": dict(result.usage),
    }


@router.post("/api/decision-lab/benchmark/runs")
def start_decision_benchmark_run(body: DecisionBenchmarkRunRequest) -> dict[str, JsonValue]:
    provider_ids = [pid.strip() for pid in body.provider_ids if pid.strip()]
    scenario_ids = [sid.strip() for sid in body.scenario_ids if sid.strip()]
    if not provider_ids:
        raise HTTPException(status_code=400, detail="Select at least one decision provider.")
    if not scenario_ids:
        raise HTTPException(status_code=400, detail="Select at least one decision scenario.")

    known_provider_ids = {str(spec["id"]) for spec in list_decision_provider_specs()}
    for pid in provider_ids:
        if pid not in known_provider_ids:
            raise HTTPException(status_code=400, detail=f"Unknown decision provider: {pid}")

    try:
        scenarios = [get_decision_scenario(sid) for sid in scenario_ids]
    except KeyError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown decision scenario: {exc.args[0]}",
        ) from exc

    run_id = uuid.uuid4().hex
    run: dict[str, JsonValue] = {
        "id": run_id,
        "status": "running",
        "started_at": time.time(),
        "completed_at": None,
        "provider_ids": list(provider_ids),
        "scenario_ids": list(scenario_ids),
        "repetitions": body.repetitions,
        "total": len(provider_ids) * len(scenarios) * body.repetitions,
        "items": [],
        "summary": compute_benchmark_summary(provider_ids, scenarios, []),
    }
    with _benchmark_lock:
        _benchmark_runs[run_id] = run

    if body.synchronous:
        _run_decision_benchmark(
            run_id,
            provider_ids,
            scenarios,
            body.repetitions,
            body.provider_options,
        )
    else:
        threading.Thread(
            target=_run_decision_benchmark,
            args=(run_id, provider_ids, scenarios, body.repetitions, body.provider_options),
            daemon=True,
        ).start()

    with _benchmark_lock:
        return dict(_benchmark_runs[run_id])


@router.get("/api/decision-lab/benchmark/runs/{run_id}")
def get_decision_benchmark_run(run_id: str) -> dict[str, JsonValue]:
    with _benchmark_lock:
        run = _benchmark_runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Decision benchmark run not found.")
        return dict(run)
