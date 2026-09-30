"""Unit tests for shared character generation and state management."""

from types import SimpleNamespace
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from providers import ImageGenerationResult, ImageProvider, ImageProviderError, SpeechProvider, TextResponseProvider
from components.image_library import ImageLibrary
from components.canvas.story_state import StoryState
from services.quirk_service import QuirkGeneratorService
from components.character_manager import Character, CharacterManager, PlayerCharacter, normalize_voice_tags
from components.notepad import Notepad


class TestNormalizeVoiceTags(unittest.TestCase):
    def test_normalizes_filters_and_deduplicates_tags(self) -> None:
        self.assertEqual(
            normalize_voice_tags([" Female ", "unknown", "female", "MALE"]),
            ["female", "male"],
        )
        self.assertEqual(normalize_voice_tags("male, FEMALE unsupported"), ["male", "female"])
        self.assertEqual(normalize_voice_tags("non-binary, nb, FEMALE"), ["nonbinary", "female"])
        self.assertEqual(normalize_voice_tags(None), [])


class TestCharacterManager(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = MagicMock(spec=TextResponseProvider)
        self.notepad = MagicMock(spec=Notepad)
        self.notepad.get_present_elements.return_value = [
            {"topic": "Quest", "info": "Recover the starblade"}
        ]
        self.image_library = MagicMock(spec=ImageLibrary)
        self.image_library.find_image_names.return_value = []
        self.image_provider = MagicMock(spec=ImageProvider)
        self.image_provider.generate.side_effect = ImageProviderError("not used by this test")
        self.speech_provider = MagicMock(spec=SpeechProvider)
        self.speech_provider.select_voice.return_value = "voice_default"
        self.story_state = StoryState()
        self.manager = CharacterManager(
            text_response_provider=self.provider,
            notepad=self.notepad,
            story_state=self.story_state,
            image_library=self.image_library,
            image_provider=self.image_provider,
            speech_provider=self.speech_provider,
        )

    def _create_character(self, name: str, description: str = "") -> str:
        return self.manager.create_or_update_character(
            name=name,
            description=description,
            personality="Resolute",
            motivation="Protect the realm",
            quirk="Counts every doorway",
            voice_tags=["female"],
        )

    def test_loads_and_normalizes_initial_characters(self) -> None:
        manager = CharacterManager(
            self.provider,
            self.notepad,
            self.story_state,
            self.image_library,
            self.image_provider,
            self.speech_provider,
        )
        characters = manager._parse_initial_characters({
            "Kaelen": {
                "description": "Ranger",
                "personality": "Stoic",
                "voice_type": "MALE, unsupported",
            }
        })
        manager.import_characters(characters)

        self.assertEqual(
            manager.get_present_characters(),
            [
                {
                    "name": "Kaelen",
                    "alias": "kaelen",
                    "gender": "male",
                    "description": "Ranger",
                    "personality": "Stoic",
                    "motivation": "",
                    "quirk": "",
                    "voice_tags": ["male"],
                    "voice_id": "voice_default",
                }
            ],
        )

    def test_limits_present_characters_without_discarding_history(self) -> None:
        manager = CharacterManager(
            self.provider,
            self.notepad,
            self.story_state,
            self.image_library,
            self.image_provider,
            self.speech_provider,
        )
        manager.max_active_characters = 2
        for name in ("One", "Two", "Three"):
            manager.create_or_update_character(
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

        self.assertEqual(listing.total_count, 2)
        self.assertEqual(len(listing.characters), 2)
        self.assertIn("Characters encountered (2 total):", listing)
        self.assertEqual(match.total_count, 1)
        self.assertEqual(match.characters[0].name, "Lyra")
        self.assertIn("Lyra", match)
        self.assertNotIn("Kaelen", match)
        pirate = self.manager.lookup_character("pirate")
        self.assertEqual(pirate.total_count, 0)
        self.assertEqual(len(pirate.characters), 0)
        self.assertIn("No characters matching", pirate)

    def test_lookup_character_name_only(self) -> None:
        self._create_character("Lyra", "A mystic scholar")
        self._create_character("Kaelen", "A forest ranger")
        self.manager.update_player_character(
            name="Aiden",
            reference="aiden_img",
            image_description="A battle-tested paladin",
        )

        # Prompt contains Lyra by name -> matches
        res = self.manager.lookup_character("Lyra casts a protective ward", name_only=True)
        self.assertEqual(len(res.characters), 1)
        self.assertEqual(res.characters[0].name, "Lyra")
        self.assertIsNone(res.player)

        # Prompt contains character description/trait ('scholar'), but name_only is True -> no match
        res_trait = self.manager.lookup_character("A mystic scholar reads a dusty book", name_only=True)
        self.assertEqual(len(res_trait.characters), 0)
        self.assertIsNone(res_trait.player)

        # Prompt mentions both Kaelen and Aiden by name -> matches both NPC and player
        res_multi = self.manager.lookup_character("Kaelen and Aiden enter the dense forest", name_only=True)
        self.assertEqual(len(res_multi.characters), 1)
        self.assertEqual(res_multi.characters[0].name, "Kaelen")
        self.assertIsNotNone(res_multi.player)
        if res_multi.player is not None:
            self.assertEqual(res_multi.player.name, "Aiden")

        # Prompt mentions 'paladin' description -> does NOT match player when name_only is True
        res_paladin = self.manager.lookup_character("The paladin raises their shield", name_only=True)
        self.assertIsNone(res_paladin.player)

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
            "components.character_manager.get_quirk_generator_service",
            return_value=quirk_service,
        ):
            profile = self.manager.create_or_update_character("Orin", "An archivist")

        self.assertEqual(profile["personality"], "Patient")
        self.assertEqual(profile["motivation"], "Find truth")
        self.assertEqual(profile["voice_tags"], ["male"])
        self.assertEqual(profile["quirk"], "Polishes a brass key")
        quirk_service.get_random_quirk.assert_called_once_with(exclude=[])
        request = self.provider.generate.call_args.args[0]
        self.assertIn("Recover the starblade", request.prompt)
        self.notepad.get_present_elements.assert_called_once_with()

    def test_generation_prompt_uses_live_speech_provider_tag_catalog(self) -> None:
        speech_provider = MagicMock(spec=SpeechProvider)
        speech_provider.get_supported_voice_tags.return_value = {
            "accent": ("British", "General American"),
            "gender": ("female", "male", "nonbinary"),
            "persona": ("Narrator",),
        }
        manager = CharacterManager(self.provider, self.notepad, self.story_state, self.image_library, self.image_provider, speech_provider)
        self.provider.generate.return_value = SimpleNamespace(
            text='{"personality":"Patient","motivation":"Find truth","gender":"female","voice_tags":["gender=female","accent=British","persona=Narrator"]}'
        )

        profile = manager.create_or_update_character("Orin")

        prompt = self.provider.generate.call_args.args[0].prompt
        self.assertIn("accent: British, General American", prompt)
        self.assertIn("persona: Narrator", prompt)
        self.assertEqual(profile.voice_tags, ["female", "gender=female", "accent=British", "persona=Narrator"])

    def test_uses_defaults_when_generation_fails(self) -> None:
        self.provider.generate.side_effect = RuntimeError("provider unavailable")
        quirk_service = MagicMock(spec=QuirkGeneratorService)
        quirk_service.get_random_quirk.return_value = "Checks the exits"

        with patch(
            "components.character_manager.get_quirk_generator_service",
            return_value=quirk_service,
        ):
            profile = self.manager.create_or_update_character("Mira")

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
            "components.character_manager.get_quirk_generator_service",
            return_value=quirk_service,
        ):
            profile = self.manager.create_or_update_character(
                "Mira",
                personality="Watchful",
                motivation="Keep everyone safe",
                voice_tags=["female"],
            )

        self.assertEqual(profile["quirk"], "Checks the exits")
        quirk_service.get_random_quirk.assert_called_once_with(
            exclude=["Counts every doorway"]
        )

    def test_create_or_update_character_mutates_state_and_notifies(self) -> None:
        result = self._create_character("Lyra")

        self.assertIn("Created character 'Lyra'", result)
        self.assertEqual(self.manager.count(), 1)

    def test_stores_character_models_by_stable_alias(self) -> None:
        self._create_character("Mara Venn")

        self.assertEqual(list(self.manager._characters), ["mara_venn"])
        self.assertIsInstance(self.manager._characters["mara_venn"], Character)

    def test_empty_character_name_does_not_mutate_or_notify(self) -> None:
        result = self._create_character("   ")

        self.assertIsNone(result)
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
        exported[0].name = "Changed"
        self.assertEqual(self.manager.get_present_characters()[0].name, "Lyra")

        self.manager.import_characters(
            [{"name": "Orin", "description": "Archivist", "voice_tags": "male", "gender": "male"}]
        )
        self.assertEqual([character["name"] for character in self.manager.export_characters()], ["Orin"])
        self.assertEqual(self.manager.export_characters()[0]["voice_tags"], ["male"])
        self.assertEqual(self.manager.export_characters()[0]["gender"], "male")

    def test_create_or_update_character_with_explicit_gender_and_nonbinary(self) -> None:
        quirk_service = MagicMock(spec=QuirkGeneratorService)
        quirk_service.get_random_quirk.return_value = "Plays with a coin"

        with patch(
            "components.character_manager.get_quirk_generator_service",
            return_value=quirk_service,
        ):
            # Explicit male
            male_prof = self.manager.create_or_update_character("Cedric", gender="male")
            self.assertEqual(male_prof["gender"], "male")
            self.assertIn("male", male_prof["voice_tags"])

            # Explicit nonbinary
            nb_prof = self.manager.create_or_update_character("Rowan", gender="nonbinary")
            self.assertEqual(nb_prof["gender"], "nonbinary")
            self.assertIn("nonbinary", nb_prof["voice_tags"])

            # Alias nb
            nb_alias = self.manager.create_or_update_character("Ash", gender="nb")
            self.assertEqual(nb_alias["gender"], "nonbinary")
            self.assertIn("nonbinary", nb_alias["voice_tags"])

    def test_get_character_voice_tags_direct_lookup(self) -> None:
        self.manager.create_or_update_character("Rowan", gender="nonbinary")
        self.assertEqual(self.manager.get_character_voice_tags("Rowan"), ["nonbinary"])
        self.assertEqual(self.manager.get_character_voice_tags("rowan"), ["nonbinary"])
        self.assertEqual(self.manager.get_character_voice_tags("Unknown"), [])

    def test_character_manager_binds_exact_reference_and_stable_voice(self) -> None:
        speech_provider = MagicMock(spec=SpeechProvider)
        speech_provider.select_voice.return_value = "voice_lyra"
        library = MagicMock(spec=ImageLibrary)
        library.find_image_names.return_value = [{
            "name": "lyra_portrait", "alias": "lyra_portrait",
            "path": "/references/lyra.png",
        }]
        manager = CharacterManager(
            self.provider, self.notepad, self.story_state, library, self.image_provider, speech_provider,
        )

        manager.create_or_update_character(
            "Lyra", personality="Curious", motivation="Learn", quirk="Hums",
            gender="female", image_reference="lyra_portrait",
        )
        # A later profile update does not silently replace either identity binding.
        manager.create_or_update_character("Lyra", personality="Brave", motivation="Learn", quirk="Hums", gender="female")

        character = manager.export_characters()[0]
        self.assertEqual(character["image_reference"], "lyra_portrait")
        self.assertEqual(character["image_reference_path"], "/references/lyra.png")
        self.assertEqual(character["voice_id"], "voice_lyra")
        speech_provider.select_voice.assert_called_once()

    def test_character_manager_generates_a_dedicated_portrait_without_image_tools(self) -> None:
        image = Image.new("RGB", (8, 8), "purple")
        payload = BytesIO()
        image.save(payload, "PNG")
        provider = MagicMock(spec=ImageProvider)
        provider.generate.return_value = ImageGenerationResult(
            image_bytes=payload.getvalue(), mime_type="image/png", provider="fast", model="portrait",
        )
        library = MagicMock(spec=ImageLibrary)
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "Mira_character.png")
            library.reference_dir = directory
            library.find_image_names.side_effect = [[], [{
                "name": "Mira_character", "alias": "Mira_character", "path": output,
            }]]
            manager = CharacterManager(
                self.provider, self.notepad, self.story_state, library, provider, self.speech_provider,
            )
            manager.create_or_update_character("Mira", personality="Alert", motivation="Help", quirk="Hums", gender="female")
            character = manager.export_characters()[0]
            self.assertEqual(character["image_reference_path"], output)
            self.assertTrue(Path(output).is_file())
        provider.generate.assert_called_once()

    def test_player_character_schema_and_aliases(self) -> None:
        player = PlayerCharacter(
            name="Aiden",
            reference="hero_ref",
            image_description="A weary wanderer in leather armor",
        )
        self.assertEqual(player.name, "Aiden")
        self.assertEqual(player.reference, "hero_ref")
        self.assertEqual(player.image_description, "A weary wanderer in leather armor")
        self.assertEqual(player.description, "A weary wanderer in leather armor")
        self.assertEqual(player.image_reference, "hero_ref")
        self.assertEqual(player["name"], "Aiden")
        self.assertEqual(player["reference"], "hero_ref")

        # Test alias coercion from dict
        player2 = PlayerCharacter.model_validate({
            "name": "Rowan",
            "description": "Silver-haired mage",
            "image_reference": "rowan_portrait",
        })
        self.assertEqual(player2.name, "Rowan")
        self.assertEqual(player2.image_description, "Silver-haired mage")
        self.assertEqual(player2.reference, "rowan_portrait")

    def test_character_manager_manages_player_character_and_binds_reference(self) -> None:
        library = MagicMock(spec=ImageLibrary)
        library.find_image_names.return_value = [{
            "name": "hero_portrait", "alias": "hero_alias",
            "path": "/references/hero.png",
        }]
        manager = CharacterManager(
            self.provider, self.notepad, self.story_state, library, self.image_provider, self.speech_provider,
        )

        self.assertIsNone(manager.get_player_character())
        self.assertIsNone(manager.get_player_reference())

        player = manager.update_player_character(
            name="Valen",
            reference="hero_portrait",
            image_description="Tall knight in etched plate armor",
        )
        self.assertEqual(player.name, "Valen")
        self.assertEqual(player.reference, "hero_alias")
        self.assertEqual(player.reference_path, "/references/hero.png")
        self.assertEqual(player.reference_source, "existing")
        self.assertEqual(manager.get_player_reference(), "hero_alias")

        # Calling update again preserves bindings unless reference is updated
        updated = manager.update_player_character(image_description="Armor now tarnished")
        self.assertEqual(updated.image_description, "Armor now tarnished")
        self.assertEqual(updated.reference, "hero_alias")
        self.assertEqual(updated.reference_path, "/references/hero.png")

    def test_character_manager_generates_player_portrait_when_missing(self) -> None:
        image = Image.new("RGB", (8, 8), "blue")
        payload = BytesIO()
        image.save(payload, "PNG")
        provider = MagicMock(spec=ImageProvider)
        provider.generate.return_value = ImageGenerationResult(
            image_bytes=payload.getvalue(), mime_type="image/png", provider="fast", model="portrait",
        )
        library = MagicMock(spec=ImageLibrary)
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "Valen_player_character.png")
            library.reference_dir = directory
            library.find_image_names.side_effect = [[], [{
                "name": "Valen_player_character", "alias": "Valen_player_character", "path": output,
            }]]
            manager = CharacterManager(
                self.provider, self.notepad, self.story_state, library, provider, self.speech_provider,
            )
            player = manager.update_player_character(
                name="Valen",
                image_description="Cloaked rogue with twin daggers",
            )
            self.assertEqual(player.reference_path, output)
            self.assertEqual(player.reference_source, "generated")
            self.assertTrue(Path(output).is_file())
        provider.generate.assert_called_once()

    def test_player_character_export_and_import(self) -> None:
        manager = CharacterManager(
            self.provider, self.notepad, self.story_state, self.image_library, self.image_provider, self.speech_provider,
        )
        manager.update_player_character(
            name="Cora",
            reference="cora_portrait",
            image_description="Alchemist with goggles",
        )
        exported = manager.export_player_character()
        self.assertIsNotNone(exported)
        self.assertEqual(exported["name"], "Cora")
        self.assertEqual(exported["image_description"], "Alchemist with goggles")

        manager2 = CharacterManager(
            self.provider, self.notepad, self.story_state, self.image_library, self.image_provider, self.speech_provider,
        )
        manager2.import_player_character(exported)
        player2 = manager2.get_player_character()
        self.assertIsNotNone(player2)
        self.assertEqual(player2.name, "Cora")

    def test_lookup_character_includes_player_character(self) -> None:
        self.manager.update_player_character(
            name="Aiden",
            reference="aiden_img",
            image_description="A battle-tested paladin",
        )
        self._create_character("Lyra", "A mystic scholar")

        listing = self.manager.lookup_character()
        self.assertEqual(listing.total_count, 2)
        self.assertEqual(len(listing.characters), 1)
        self.assertIsNotNone(listing.player)
        if listing.player is not None:
            self.assertEqual(listing.player.name, "Aiden")
        self.assertIn("Characters encountered (2 total):", listing)
        self.assertIn("[Player Character] Aiden:", listing)
        self.assertIn("Lyra", listing)

        match_player = self.manager.lookup_character("paladin")
        self.assertEqual(match_player.total_count, 1)
        self.assertIsNotNone(match_player.player)
        if match_player.player is not None:
            self.assertEqual(match_player.player.name, "Aiden")
        self.assertEqual(len(match_player.characters), 0)
        self.assertIn("Aiden", match_player)
        self.assertNotIn("Lyra", match_player)

    def test_create_or_update_character_upsert_existing_updates_only_specified_fields(self) -> None:
        self.manager.create_or_update_character(
            name="Orin",
            description="An archivist",
            personality="Patient",
            motivation="Find truth",
            quirk="Polishes a brass key",
            gender="male",
            voice_tags=["male"],
        )
        self.provider.generate.reset_mock()

        # Update description and personality only; leave motivation, quirk, gender, voice unspecified
        updated = self.manager.create_or_update_character(
            name="Orin",
            description="Chief Archivist of the High Library",
            personality="Obsessive and perfectionist",
        )

        self.assertIsNotNone(updated)
        self.provider.generate.assert_not_called()
        self.assertEqual(updated.description, "Chief Archivist of the High Library")
        self.assertEqual(updated.personality, "Obsessive and perfectionist")
        self.assertEqual(updated.motivation, "Find truth")
        self.assertEqual(updated.quirk, "Polishes a brass key")
        self.assertEqual(updated.gender, "male")
        self.assertEqual(updated.voice_tags, ["male"])
        self.assertEqual(self.manager.count(), 1)

    def test_create_or_update_character_upsert_updates_gender_and_voice_tags(self) -> None:
        self.manager.create_or_update_character(
            name="Rowan",
            description="A wandering healer",
            personality="Empathetic",
            motivation="Heal the afflicted",
            quirk="Collects herbs in pockets",
            gender="female",
            voice_tags=["female"],
        )
        self.provider.generate.reset_mock()

        updated = self.manager.create_or_update_character(
            name="Rowan",
            gender="nonbinary",
        )

        self.assertIsNotNone(updated)
        self.provider.generate.assert_not_called()
        self.assertEqual(updated.gender, "nonbinary")
        self.assertIn("nonbinary", updated.voice_tags)
        self.assertNotIn("female", updated.voice_tags)
        self.assertEqual(updated.personality, "Empathetic")

    def test_create_or_update_character_prioritizes_serialized_story_state_across_initializations(self) -> None:
        # Simulate serialized story state loaded from previous session/persistence
        serialized_story_state = {
            "story_planning_state": {
                "characters": [
                    {
                        "name": "Boran",
                        "alias": "boran",
                        "gender": "male",
                        "description": "Veteran gatekeeper of Ironhold",
                        "personality": "Stoic and unwavering",
                        "motivation": "Protect the city gates",
                        "quirk": "Chews dried mint leaves",
                        "voice_tags": ["male"],
                        "voice_id": "voice_boran_unique",
                        "image_reference": "boran_face_ref",
                    }
                ]
            }
        }
        story_state = StoryState()
        story_state.load(serialized_story_state)
        self.manager.story_state = story_state
        self.assertEqual(self.manager.count(), 0)
        self.provider.generate.reset_mock()

        # Story module views propose creating Boran with contradictory traits
        profile = self.manager.create_or_update_character(
            name="Boran",
            description="A young, timid sentry",
            personality="Cowardly and easily frightened",
            motivation="Flee from danger",
            quirk="Fidgets nervously",
            gender="female",
        )

        self.assertIsNotNone(profile)
        # LLM text provider shouldn't need to generate missing traits
        self.provider.generate.assert_not_called()

        # Serialized traits must take priority over story module's views for consistency
        self.assertEqual(profile.gender, "male")
        self.assertEqual(profile.personality, "Stoic and unwavering")
        self.assertEqual(profile.motivation, "Protect the city gates")
        self.assertEqual(profile.quirk, "Chews dried mint leaves")
        self.assertEqual(profile.description, "Veteran gatekeeper of Ironhold")
        self.assertEqual(profile.voice_id, "voice_boran_unique")
        self.assertEqual(profile.image_reference, "boran_face_ref")
        self.assertEqual(self.manager.count(), 1)

    def test_create_or_update_character_partial_serialized_fills_missing_from_views_or_generation(self) -> None:
        serialized_story_state = {
            "story_planning_state": {
                "characters": [
                    {
                        "name": "Kael",
                        "alias": "kael",
                        "gender": "nonbinary",
                        "voice_tags": ["nonbinary"],
                        "personality": "Stealthy rogue",
                    }
                ]
            }
        }
        story_state = StoryState()
        story_state.load(serialized_story_state)
        self.manager.story_state = story_state
        quirk_service = MagicMock(spec=QuirkGeneratorService)
        quirk_service.get_random_quirk.return_value = "Always counts coins twice"

        # Story module specifies motivation, but quirk is missing in both
        with patch(
            "components.character_manager.get_quirk_generator_service",
            return_value=quirk_service,
        ):
            profile = self.manager.create_or_update_character(
                name="Kael",
                description="Shadow operative",
                motivation="Recover the stolen ledger",
            )

        self.assertIsNotNone(profile)
        # Serialized traits prioritized
        self.assertEqual(profile.gender, "nonbinary")
        self.assertEqual(profile.personality, "Stealthy rogue")
        # Missing from serialized filled by story module view
        self.assertEqual(profile.motivation, "Recover the stolen ledger")
        self.assertEqual(profile.description, "Shadow operative")
        # Missing quirk generated
        self.assertEqual(profile.quirk, "Always counts coins twice")

    def test_get_character_references(self) -> None:
        manager = CharacterManager(
            self.provider, self.notepad, self.story_state, self.image_library, self.image_provider, self.speech_provider
        )
        self.assertEqual(manager.get_character_references(), [])

        manager.set_player_character(
            PlayerCharacter(name="Aiden", reference="hero_portrait", reference_path="/references/hero.png")
        )
        self.assertEqual(manager.get_character_references(), ["hero_portrait", "/references/hero.png"])

        manager.create_or_update_character(
            "Lyra",
            personality="Curious",
            motivation="Explore",
            quirk="Hums",
            gender="female",
            image_reference="lyra_portrait",
        )
        # Lyra portrait alias and deduplication
        refs = manager.get_character_references()
        self.assertIn("hero_portrait", refs)
        self.assertIn("lyra_portrait", refs)
        self.assertEqual(len(refs), len(set(r.casefold() for r in refs)))

    def test_character_manager_resolves_existing_reference_for_character_name_slug(self) -> None:
        provider = MagicMock(spec=ImageProvider)
        library = MagicMock(spec=ImageLibrary)
        library.find_image_names.return_value = [
            {
                "name": "lady_lux",
                "alias": "lady_lux",
                "path": "/theaters/references/lady_lux.jpg",
                "title": "Lady Lux",
                "description": "Lady Lux portrait",
            }
        ]
        manager = CharacterManager(
            self.provider, self.notepad, self.story_state, library, provider, self.speech_provider,
        )

        char = manager.create_or_update_character(
            "Lady Lux",
            personality="Glamorous",
            motivation="Freedom",
            quirk="Plays sax riffs",
            gender="female",
        )

        self.assertIsNotNone(char)
        self.assertEqual(char["image_reference"], "lady_lux")
        self.assertEqual(char["image_reference_path"], "/theaters/references/lady_lux.jpg")
        self.assertEqual(char["image_reference_source"], "existing")
        # Should NOT have called image provider to generate a fallback portrait
        provider.generate.assert_not_called()

    def test_character_manager_prefers_base_reference_over_generated_character_png(self) -> None:
        provider = MagicMock(spec=ImageProvider)
        library = MagicMock(spec=ImageLibrary)
        library.find_image_names.return_value = [
            {
                "name": "Lady_Lux_character",
                "alias": "Lady_Lux_character",
                "path": "/theaters/references/Lady_Lux_character.png",
                "title": "Generated Portrait",
                "description": "Generated",
            },
            {
                "name": "lady_lux",
                "alias": "lady_lux",
                "path": "/theaters/references/lady_lux.jpg",
                "title": "Lady Lux",
                "description": "Lady Lux reference",
            },
        ]
        manager = CharacterManager(
            self.provider, self.notepad, self.story_state, library, provider, self.speech_provider,
        )

        char = manager.create_or_update_character(
            "Lady Lux",
            personality="Glamorous",
            motivation="Freedom",
            quirk="Plays sax riffs",
            gender="female",
        )

        self.assertIsNotNone(char)
        self.assertEqual(char["image_reference"], "lady_lux")
        self.assertEqual(char["image_reference_path"], "/theaters/references/lady_lux.jpg")
        self.assertEqual(char["image_reference_source"], "existing")
        provider.generate.assert_not_called()

    def test_character_manager_resolves_player_character_by_slug(self) -> None:
        provider = MagicMock(spec=ImageProvider)
        library = MagicMock(spec=ImageLibrary)
        library.find_image_names.return_value = [
            {
                "name": "retro_pulsar",
                "alias": "retro_pulsar",
                "path": "/theaters/references/retro_pulsar.jpg",
                "title": "Retro Pulsar",
                "description": "Retro Pulsar suit",
            }
        ]
        manager = CharacterManager(
            self.provider, self.notepad, self.story_state, library, provider, self.speech_provider,
        )

        player = manager.update_player_character(
            name="Retro Pulsar",
            image_description="Astronaut in 1960s suit",
        )

        self.assertEqual(player.name, "Retro Pulsar")
        self.assertEqual(player.reference, "retro_pulsar")
        self.assertEqual(player.reference_path, "/theaters/references/retro_pulsar.jpg")
        self.assertEqual(player.reference_source, "existing")
        provider.generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
