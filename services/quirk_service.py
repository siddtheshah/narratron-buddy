"""Random Quirk Generator Service for Adventure Mode characters.

Provides a rich catalog of speech, behavioral, emotional, and oddity quirks to ensure
characters generated in story planning have distinct, memorable, and non-repetitive traits.
Registered in central object_registry for application-wide sharing and future database integration.
"""

import random
from typing import Any, List, Optional

SPEECH_QUIRKS = [
    "Speaks in overly grand, archaic vocabulary regardless of the situation.",
    "Always ends statements with a soft, trailing rhetorical question.",
    "Refuses to use contractions and speaks with rhythmic, measured precision.",
    "Whispers conspiratorially whenever discussing important choices or plans.",
    "Refers to themselves strictly in the third person.",
    "Frequently quotes non-existent ancient proverbs with absolute confidence.",
    "Repeats the last three words of their sentence when excited or tense.",
    "Uses dramatic theatrical metaphors for everyday mundane tasks.",
    "Speaks in rapid-fire staccato sentences when under pressure.",
    "Frequently uses obscure nautical navigation terms to describe land routes.",
]

BEHAVIORAL_QUIRKS = [
    "Flips a worn silver coin before agreeing to any high-stakes choice.",
    "Fidgets compulsively with an intricate brass pocket watch.",
    "Polishes a tiny metal trinket in their pocket whenever nervous.",
    "Never sits or stands with their back toward an open door or passage.",
    "Taps rhythmically on surfaces to measure time before speaking.",
    "Rearranges small objects nearby into neat geometric patterns.",
    "Takes meticulous handwritten notes in a battered, ink-stained journal.",
    "Humms a faint, haunting lullaby whenever walking through doorways.",
    "Adjusts their gloves or collar meticulously before delivering news.",
    "Collects small, polished river pebbles and keeps them in their pockets.",
]

RELATIONAL_QUIRKS = [
    "Instantly trusts anyone who offers them warm tea or baked goods.",
    "Is deeply suspicious of inanimate objects that look 'too symmetrical'.",
    "Treats stray animals and pets like visiting noble dignitaries.",
    "Dramatically over-exaggerates minor inconveniences as ancient curses.",
    "Names all their tools and favorite possessions after childhood friends.",
    "Apologizes politely to furniture after accidentally bumping into it.",
    "Slightly bows to allies before answering a serious question.",
    "Refuses to mention bad news without first knocking on wood twice.",
]

ODDITY_QUIRKS = [
    "Claims to smell incoming rain or storms hours before any clouds arrive.",
    "Insists every creak in floorboards is a ghost trying to pass a message.",
    "Refers to shadows as 'old acquaintances' and greets them quietly.",
    "Measures all distances strictly in cat lengths or boot steps.",
    "Refuses to cross bridges without first tossing a small copper coin in.",
    "Insists that wearing hats indoors drains one's creative energy.",
    "Claims they can tell a person's mood by the scent of their cloak.",
    "Always carries a small pocket compass even in familiar rooms.",
]

ALL_QUIRKS: List[str] = (
    SPEECH_QUIRKS + BEHAVIORAL_QUIRKS + RELATIONAL_QUIRKS + ODDITY_QUIRKS
)


class QuirkGeneratorService:
    """Service to generate randomized distinct character quirks."""

    def __init__(
        self,
        quirks_catalog: Optional[List[str]] = None,
        database_manager: Optional[Any] = None,
    ):
        self._catalog = list(quirks_catalog or ALL_QUIRKS)
        self.database_manager = database_manager

    def get_catalog(self) -> List[str]:
        """Return full catalog of quirks."""
        return list(self._catalog)

    def add_quirk(self, quirk: str) -> None:
        """Add a new custom or user-submitted quirk to the runtime catalog."""
        clean = str(quirk or "").strip()
        if clean and clean not in self._catalog:
            self._catalog.append(clean)

    def get_random_quirk(self, exclude: Optional[List[str]] = None) -> str:
        """Return a random quirk from the catalog, excluding any listed in `exclude`.

        Args:
            exclude: Optional list of quirks to filter out (e.g. currently active character quirks).

        Returns:
            A distinct quirk string.
        """
        excluded_set = {str(q).strip().lower() for q in (exclude or []) if q}
        available = [q for q in self._catalog if q.strip().lower() not in excluded_set]

        if not available:
            # If all catalog quirks are excluded, fallback to selecting from full catalog
            available = self._catalog

        return random.choice(available)

    def get_random_quirks(self, count: int, exclude: Optional[List[str]] = None) -> List[str]:
        """Return `count` distinct random quirks."""
        result: List[str] = []
        current_exclude = list(exclude or [])
        for _ in range(max(1, count)):
            quirk = self.get_random_quirk(exclude=current_exclude)
            result.append(quirk)
            current_exclude.append(quirk)
        return result


# Shared registered instance for the quirk service
quirk_service = QuirkGeneratorService()


def get_quirk_generator_service() -> QuirkGeneratorService:
    """Return shared QuirkGeneratorService instance."""
    return quirk_service
