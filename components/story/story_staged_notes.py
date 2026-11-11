"""Role-scoped tools stage updates until their complete stage can commit."""

import logging

from components.notepad import Notepad, StickyOwner


logger = logging.getLogger(__name__)


class StagedStoryNotes:
    def __init__(self, notepad: Notepad, owner: StickyOwner) -> None:
        self.notepad = notepad
        self.owner = owner
        self.scoped = notepad.for_role(owner)
        self.updates: dict[str, str] = {}

    def check_schema(self, topic: str) -> str:
        """Read the schema of a topic owned by this role."""
        return self.scoped.check_schema(topic)

    def update_sticky_note(self, topic: str, info: str) -> str:
        """Stage one validated sticky value for atomic stage completion."""
        topic, info = topic.strip(), info.strip()
        try:
            self.notepad.validate_role_update(self.owner, topic, info)
        except ValueError as exc:
            logger.warning(
                "[StagedStoryNotes] Rejected sticky update (owner=%s, topic=%s): %s",
                self.owner,
                topic,
                exc,
            )
            return f"Error: {exc}"
        self.updates[topic] = info
        logger.debug("[StagedStoryNotes] Staged sticky update (owner=%s, topic=%s)", self.owner, topic)
        return f"Sticky note '{topic}' staged."
