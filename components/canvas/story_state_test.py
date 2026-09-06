from components.canvas.story_state import StoryState


def test_story_state_isolated_from_other_theaters() -> None:
    first, second = StoryState(), StoryState()
    first.named_elements.append({"name": "Ada"})
    assert second.named_elements == []
