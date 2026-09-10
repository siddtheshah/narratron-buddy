"""Unit tests for shared character generation and state management."""

from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from providers import TextResponseProvider
from services.quirk_service import QuirkGeneratorService
from tools.story.character_manager import CharacterManager, normalize_voice_tags
from tools.story.notepad import Notepad


class TestNormalizeVoiceTags(unittest.TestCase):
    def test_normalizes_filters_and_deduplicates_tags(self) -> None:
        self.assertEqual(
            normalize_voice_tags([" Female ", "unknown", "female", "MALE"]),
            ["female", "male"],
        )
        self.assertEqual(normalize_voice_tags("male, FEMALE unsupported"), ["male", "female"])
        self.assertEqual(normalize_voice_tags(None), [])


class TestCharacterManager(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = MagicMock(spec=TextResponseProvider)
        self.notepad = MagicMock(spec=Notepad)
        self.notepad.get_present_elements.return_value = [
            {"topic": "Quest", "info": "Recover the starblade"}
        ]
        self.manager = CharacterManager(
            text_response_provider=self.provider,
            notepad=self.notepad,
        )

    def _create_character(self, name: str, description: str = "") -> str:
        return self.manager.generate_character(
            name=name,
            description=description,
            personality="Resolute",
            motivation="Protect the realm",
            quirk="Counts every doorway",
            voice_tags=["female"],
        )

    def test_requires_text_response_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "text_response_provider is required"):
            CharacterManager(None, self.notepad)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "notepad is required"):
            CharacterManager(self.provider, None)  # type: ignore[arg-type]

    def test_loads_and_normalizes_initial_characters(self) -> None:
        manager = CharacterManager(
            self.provider,
            self.notepad,
            config={
                "initial_characters": {
                    "Kaelen": {
                        "description": "Ranger",
                        "personality": "Stoic",
                        "voice_type": "MALE, unsupported",
                    }
                }
            },
        )

        self.assertEqual(
            manager.get_present_characters(),
            [
                {
                    "name": "Kaelen",
                    "description": "Ranger",
                    "personality": "Stoic",
                    "motivation": "",
                    "quirk": "",
                    "voice_tags": ["male"],
                }
            ],
        )

    def test_limits_present_characters_without_discarding_history(self) -> None:
        manager = CharacterManager(self.provider, self.notepad, config={"max_active_characters": 2})
        for name in ("One", "Two", "Three"):
            manager.generate_character(
                name=name,
                personality="Steady",
                motivation="Help",
                quirk="Hums",
                voice_tags="male",
            )

        self.assertEqual(
            [character["name"] for character in manager.get_present_characters()],
            ["Two", "Three"],
        )
        self.assertEqual(len(manager.export_characters()), 3)

    def test_lookup_lists_and_searches_all_session_characters(self) -> None:
        self._create_character("Lyra", "A mystic scholar")
        self._create_character("Kaelen", "A forest ranger")

        listing = self.manager.lookup_character()
        match = self.manager.lookup_character("scholar")

        self.assertIn("Characters encountered (2 total):", listing)
        self.assertIn("Lyra", match)
        self.assertNotIn("Kaelen", match)
        self.assertIn("No characters matching", self.manager.lookup_character("pirate"))

    def test_generates_missing_profile_fields_from_provider_and_planning_elements(self) -> None:
        self.provider.generate.return_value = SimpleNamespace(
            text=(
                "```json\n"
                '{"personality":"Patient","motivation":"Find truth",'
                '"voice_tags":["male"]}\n'
                "```"
            )
        )
        quirk_service = MagicMock(spec=QuirkGeneratorService)
        quirk_service.get_random_quirk.return_value = "Polishes a brass key"

        with patch(
            "tools.story.character_manager.get_quirk_generator_service",
            return_value=quirk_service,
        ):
            profile = self.manager.generate_character_profile("Orin", "An archivist")

        self.assertEqual(profile["personality"], "Patient")
        self.assertEqual(profile["motivation"], "Find truth")
        self.assertEqual(profile["voice_tags"], ["male"])
        self.assertEqual(profile["quirk"], "Polishes a brass key")
        quirk_service.get_random_quirk.assert_called_once_with(exclude=[])
        request = self.provider.generate.call_args.args[0]
        self.assertIn("Recover the starblade", request.prompt)
        self.notepad.get_present_elements.assert_called_once_with()

    def test_uses_defaults_when_generation_fails(self) -> None:
        self.provider.generate.side_effect = RuntimeError("provider unavailable")
        quirk_service = MagicMock(spec=QuirkGeneratorService)
        quirk_service.get_random_quirk.return_value = "Checks the exits"

        with patch(
            "tools.story.character_manager.get_quirk_generator_service",
            return_value=quirk_service,
        ):
            profile = self.manager.generate_character_profile("Mira")

        self.assertEqual(profile["personality"], "Enigmatic and watchful.")
        self.assertEqual(profile["motivation"], "Survive and prosper in the current scene.")
        self.assertEqual(profile["voice_tags"], ["female"])
        self.assertEqual(profile["quirk"], "Checks the exits")
        quirk_service.get_random_quirk.assert_called_once_with(exclude=[])

    def test_generated_quirk_excludes_quirks_of_present_characters(self) -> None:
        self._create_character("Lyra")
        quirk_service = MagicMock(spec=QuirkGeneratorService)
        quirk_service.get_random_quirk.return_value = "Checks the exits"

        with patch(
            "tools.story.character_manager.get_quirk_generator_service",
            return_value=quirk_service,
        ):
            profile = self.manager.generate_character_profile(
                "Mira",
                personality="Watchful",
                motivation="Keep everyone safe",
                voice_tags=["female"],
            )

        self.assertEqual(profile["quirk"], "Checks the exits")
        quirk_service.get_random_quirk.assert_called_once_with(
            exclude=["Counts every doorway"]
        )

    def test_generate_character_mutates_state_and_notifies(self) -> None:
        result = self._create_character("Lyra")

        self.assertIn("Created character 'Lyra'", result)
        self.assertEqual(self.manager.count(), 1)

    def test_empty_character_name_does_not_mutate_or_notify(self) -> None:
        result = self._create_character("   ")

        self.assertEqual(result, "Character name cannot be empty.")
        self.assertEqual(self.manager.count(), 0)

    def test_applies_at_most_two_character_updates(self) -> None:
        updates = [
            {
                "name": name,
                "personality": "Alert",
                "motivation": "Investigate",
                "quirk": "Tilts their head",
                "voice_tags": ["male"],
            }
            for name in ("One", "Two", "Three")
        ]

        manifested = self.manager.apply_character_updates(updates)

        self.assertEqual([character["name"] for character in manifested], ["One", "Two"])
        self.assertEqual(self.manager.count(), 2)

    def test_clear_scene_returns_count_and_notifies(self) -> None:
        self._create_character("Lyra")
        self._create_character("Kaelen")

        removed = self.manager.clear_scene()

        self.assertEqual(removed, 2)
        self.assertEqual(self.manager.get_present_characters(), [])

    def test_export_is_defensive_and_import_replaces_state(self) -> None:
        self._create_character("Lyra")
        exported = self.manager.export_characters()
        exported[0]["name"] = "Changed"
        self.assertEqual(self.manager.get_present_characters()[0]["name"], "Lyra")

        self.manager.import_characters(
            [{"name": "Orin", "description": "Archivist", "voice_tags": "male"}]
        )
        self.assertEqual([character["name"] for character in self.manager.export_characters()], ["Orin"])
        self.assertEqual(self.manager.export_characters()[0]["voice_tags"], ["male"])


if __name__ == "__main__":
    unittest.main()
