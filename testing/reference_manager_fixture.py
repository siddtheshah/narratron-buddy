"""Reference manager fixture with real resolution and mocked character lookups."""

from components.canvas.story_state import StoryState
from unittest.mock import MagicMock

from components.reference_manager import CharacterLookupResult, ReferenceManager
from components.theater_manager import Theater


def make_reference_manager(theater: Theater, *, story_state: StoryState) -> ReferenceManager:
    """Exercise manager resolution while isolating character discovery and storage."""
    manager = ReferenceManager(theater, story_state=story_state)
    manager.lookup_character = MagicMock(
        spec=manager.lookup_character, return_value=CharacterLookupResult()
    )
    manager.get_character_references = MagicMock(
        spec=manager.get_character_references, return_value=[]
    )
    manager.get_latest_reference_path_for_character = MagicMock(
        spec=manager.get_latest_reference_path_for_character, return_value=None
    )
    return manager
