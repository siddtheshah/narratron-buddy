"""Persisted story-planning and spoken-scene state."""



class StoryState:
    def __init__(self) -> None:
        self.named_elements: list[dict[str, str]] = []
        self.story_planning_state: dict[str, object] = {}
        self.scene_dialogue: list[dict[str, str]] = []
        self.narration = ""; self.narration_spans: list[dict[str, object]] = []
        self.character_voice_assignments: dict[str, str] = {}

    def sticky_notes(self) -> list[dict[str, str]]:
        notes = self.story_planning_state.get("sticky_notes", self.named_elements)
        return [note for note in notes if isinstance(note, dict)] if isinstance(notes, list) else []

    def load(self, data: dict[str, object]) -> None:
        for name in ("named_elements", "scene_dialogue", "narration_spans"):
            value = data.get(name, []); setattr(self, name, list(value) if isinstance(value, list) else [])
        self.narration = str(data.get("narration") or "")
        planning = data.get("story_planning_state", {}); self.story_planning_state = dict(planning) if isinstance(planning, dict) else {}
        assignments = data.get("character_voice_assignments", {}); self.character_voice_assignments = dict(assignments) if isinstance(assignments, dict) else {}

    def serialize(self) -> dict[str, object]:
        return {"named_elements": self.named_elements, "story_planning_state": self.story_planning_state,
                "scene_dialogue": self.scene_dialogue, "narration": self.narration,
                "narration_spans": self.narration_spans, "character_voice_assignments": self.character_voice_assignments}

    def payload(self) -> dict[str, object]:
        return {"scene_dialogue": self.scene_dialogue, "narration": self.narration,
                "narration_spans": self.narration_spans, "character_voice_assignments": self.character_voice_assignments}
