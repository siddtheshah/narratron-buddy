"""Fixed, repeatable text prompts used to compare provider text generation behavior."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class BenchmarkTextPrompt:
    id: str
    title: str
    dimension: str
    prompt: str
    system_instruction: str | None = None
    temperature: float | None = 0.7
    max_output_tokens: int | None = 500


TEXT_PROMPTS = (
    BenchmarkTextPrompt(
        id="dm-adventure-intro",
        title="DM Adventure Scene Opening",
        dimension="Narrative Atmosphere & Hook",
        prompt="The party steps out of the damp cavern into the bioluminescent forest of Elderglow. Describe the sight, sounds, and immediate environmental hazard ahead.",
        system_instruction="You are an expert tabletop RPG Dungeon Master. Provide vivid, atmospheric scene descriptions with clear sensory details and actionable hooks.",
        temperature=0.7,
        max_output_tokens=1000,
    ),
    BenchmarkTextPrompt(
        id="player-choice-resolution",
        title="Player Action & Consequence",
        dimension="Action Resolution & Branching",
        prompt="The rogue attempts to pick the lock on the iron-bound chest while the guard captain's boots echo down the hallway. The sleight of hand roll was a partial success (12 vs DC 15). Resolve this complication dynamically.",
        system_instruction="You are a dynamic narrative assistant for an interactive RPG. Handle partial success with compelling plot twists and dramatic tension.",
        temperature=0.8,
        max_output_tokens=1000,
    ),
    BenchmarkTextPrompt(
        id="npc-dialogue-interaction",
        title="NPC Persona & Dialogue",
        dimension="Character Voice & Roleplay",
        prompt="The party confronts Master Thaddeus, a secretive alchemist who secretly built the clockwork guardian. He is defensive, highly intelligent, and speaks with nervous speed. Respond to the party asking why his insignia was found on the automaton.",
        system_instruction="You are roleplaying an NPC in an interactive story. Stay strictly in character, using speech quirks, emotional tone, and believable motives.",
        temperature=0.75,
        max_output_tokens=1000,
    ),
    BenchmarkTextPrompt(
        id="worldbuilding-lore",
        title="World-building Lore Snippet",
        dimension="Creativity & World Design",
        prompt="Detail the legend of the Sunken Citadel of Oakhaven: how it was lost, what ancient artifact reposes at its core, and why rumors say its bells ring during solar eclipses.",
        system_instruction="You are a fantasy historian and storyteller. Write rich, evocative lore with distinct proper nouns and historical depth.",
        temperature=0.7,
        max_output_tokens=1500,
    ),
    BenchmarkTextPrompt(
        id="structured-encounter-json",
        title="Structured Encounter Data",
        dimension="Instruction Following & Formatting",
        prompt="Generate a random wilderness encounter for a level 4 party traveling through a haunted marshland. Format your output as valid JSON with keys: 'title', 'difficulty', 'summary', 'monsters' (list of objects with 'name' and 'count'), and 'loot' (list of strings).",
        system_instruction="You are a game mechanic generator. Output ONLY raw valid JSON with no markdown block markers or conversational preamble.",
        temperature=0.3,
        max_output_tokens=1000,
    ),
    BenchmarkTextPrompt(
        id="fast-tactical-summary",
        title="Fast Tactical Combat Turn",
        dimension="Conciseness & Speed",
        prompt="Summarize the state of the battle after a dragon casts a breath weapon over the vanguard. State health impacts, battlefield terrain changes, and next turn options in bullet points.",
        system_instruction="Be extremely concise, direct, and tactical.",
        temperature=0.5,
        max_output_tokens=600,
    ),
)



def text_prompt_catalog() -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "title": item.title,
            "dimension": item.dimension,
            "prompt": item.prompt,
            "system_instruction": item.system_instruction,
            "temperature": item.temperature,
            "max_output_tokens": item.max_output_tokens,
        }
        for item in TEXT_PROMPTS
    ]


def get_text_prompt(prompt_id: str) -> BenchmarkTextPrompt:
    for item in TEXT_PROMPTS:
        if item.id == prompt_id:
            return item
    raise KeyError(prompt_id)
