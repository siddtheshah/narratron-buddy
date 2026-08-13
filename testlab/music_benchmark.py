"""Fixed, repeatable music prompts used to compare provider audio generation behavior."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class BenchmarkMusicPrompt:
    id: str
    title: str
    dimension: str
    prompt: str
    duration_seconds: float = 30.0
    tempo: str | None = None
    genre: str | None = None


MUSIC_PROMPTS = (
    BenchmarkMusicPrompt(
        id="cinematic-orchestral",
        title="Cinematic Orchestral Theme",
        dimension="Orchestral & Dynamics",
        prompt="An epic cinematic orchestral soundtrack featuring swelling strings, french horns, and thunderous timpani drums, building up to a dramatic climax for an ancient ruin discovery.",
        duration_seconds=30.0,
        tempo="Moderato",
        genre="Cinematic Orchestral",
    ),
    BenchmarkMusicPrompt(
        id="lofi-ambient",
        title="Lofi Ambient Chill",
        dimension="Atmosphere & Texture",
        prompt="Cozy lofi hip hop beat with warm vinyl crackle, mellow electric piano chords, soft woodwind flourishes, and a slow relaxed drum groove.",
        duration_seconds=30.0,
        tempo="Adagio (75 BPM)",
        genre="Lofi Ambient",
    ),
    BenchmarkMusicPrompt(
        id="cyberpunk-synthwave",
        title="Cyberpunk Synthwave Action",
        dimension="Rhythm & Synth Polish",
        prompt="Driving cyberpunk synthwave track with arpeggiated basslines, distorted analog synths, punchy 80s drums, and soaring neon leads.",
        duration_seconds=30.0,
        tempo="Allegro (125 BPM)",
        genre="Synthwave",
    ),
    BenchmarkMusicPrompt(
        id="whimsical-acoustic",
        title="Whimsical Folk Fantasy",
        dimension="Melody & Acoustic Polish",
        prompt="Playful acoustic folk melody with fingerpicked guitar, wooden flute, light tambourine, and whimsical marimba counterpoint for a peaceful village marketplace.",
        duration_seconds=30.0,
        tempo="Andante",
        genre="Acoustic Folk",
    ),
    BenchmarkMusicPrompt(
        id="dark-suspense",
        title="Dark Suspense Drone",
        dimension="Suspense & Tension",
        prompt="Tense suspenseful ambient drone with low cello swells, eerie metallic reverberations, subtle heartbeats, and unexpected piano notes.",
        duration_seconds=30.0,
        tempo="Slow Drone",
        genre="Dark Ambient",
    ),
    BenchmarkMusicPrompt(
        id="heroic-fanfare",
        title="Heroic Victory Fanfare",
        dimension="Climax & Brass Brightness",
        prompt="Triumphant brass fanfare with soaring trumpets, marching snare drums, and crashing cymbals celebrating a heroic adventure victory.",
        duration_seconds=15.0,
        tempo="Presto (140 BPM)",
        genre="Fanfare",
    ),
)


def music_prompt_catalog() -> list[dict[str, object]]:
    return [
        {
            "id": item.id,
            "title": item.title,
            "dimension": item.dimension,
            "prompt": item.prompt,
            "duration_seconds": item.duration_seconds,
            "tempo": item.tempo,
            "genre": item.genre,
        }
        for item in MUSIC_PROMPTS
    ]


def get_music_prompt(prompt_id: str) -> BenchmarkMusicPrompt:
    for item in MUSIC_PROMPTS:
        if item.id == prompt_id:
            return item
    raise KeyError(prompt_id)
