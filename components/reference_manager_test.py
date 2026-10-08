"""Unit tests for shared character generation and state management."""

from types import SimpleNamespace
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image

from providers import ImageGenerationResult, ImageProvider, ImageProviderError, SpeechProvider, TextResponseProvider
from components.canvas.story_state import StoryState
from services.quirk_service import QuirkGeneratorService
from components.reference_manager import Character, ReferenceManager, PlayerCharacter, normalize_voice_tags
from components.notepad import Notepad
from components.theater_manager import Theater, TheaterManager
from tools.reference_utils import resolve_provider_references


class TestNormalizeVoiceTags(unittest.TestCase):
    def test_normalizes_filters_and_deduplicates_tags(self) -> None:
        self.assertEqual(
            normalize_voice_tags([" Female ", "unknown", "female", "MALE"]),
            ["female", "male"],
        )
        self.assertEqual(normalize_voice_tags("male, FEMALE unsupported"), ["male", "female"])
        self.assertEqual(normalize_voice_tags("non-binary, nb, FEMALE"), ["nonbinary", "female"])
        self.assertEqual(normalize_voice_tags(None), [])


class TestReferenceManager(unittest.TestCase):
    def test_model_context_hides_bindings_without_changing_persisted_characters(self) -> None:
        character = Character(
            name="Arthur Modella", gender="male", description="A wizard",
            image_reference="portrait_alias", image_reference_path="private/Arthur/2.png",
            image_reference_source="generated",
        )
        player = PlayerCharacter(
            name="Grim Vallos", image_description="A knight", reference="player_alias",
            reference_path="private/Grim/1.png", reference_source="existing",
        )
        lookup = self.manager.lookup_character()
        lookup.characters = [character]
        lookup.player = player
        model_context = lookup.for_model_context()

        self.assertEqual(model_context.characters[0].name, "Arthur Modella")
        self.assertEqual(model_context.characters[0].description, "A wizard")
        self.assertIsNone(model_context.characters[0].image_reference)
        self.assertIsNone(model_context.characters[0].image_reference_path)
        self.assertIsNone(model_context.characters[0].image_reference_source)
        self.assertEqual(model_context.player.image_description, "A knight")
        self.assertIsNone(model_context.player.reference)
        self.assertIsNone(model_context.player.reference_path)
        self.assertIsNone(model_context.player.reference_source)
        self.assertEqual(character.image_reference_path, "private/Arthur/2.png")
        self.assertEqual(player.reference_path, "private/Grim/1.png")
        self.assertNotIn("player_alias", player.describe())
        self.assertNotIn("player_alias", lookup.describe())
        self.assertNotIn("private/", model_context.model_dump_json(exclude_none=True))

    def setUp(self) -> None:
        self.theater = MagicMock(spec=Theater)
        self.theater.characters_dir.return_value = Path("/nonexistent/base_characters")
        self.theater.updated_characters_dir.return_value = Path("/nonexistent/characters")
        self.theater.references_dir.return_value = Path("/nonexistent/references")
        self.provider = MagicMock(spec=TextResponseProvider)
        self.notepad = MagicMock(spec=Notepad)
        self.notepad.get_present_elements.return_value = [
            {"topic": "Quest", "info": "Recover the starblade"}
        ]
        self.image_provider = MagicMock(spec=ImageProvider)
        self.image_provider.generate.side_effect = ImageProviderError("not used by this test")
        self.speech_provider = MagicMock(spec=SpeechProvider)
        self.speech_provider.select_voice.return_value = "voice_default"
        self.story_state = StoryState()
        self.manager = ReferenceManager(
            theater=self.theater,
            text_response_provider=self.provider,
            notepad=self.notepad,
            story_state=self.story_state,
            image_provider=self.image_provider,
            speech_provider=self.speech_provider,
        )

    def test_requires_theater(self) -> None:
        with self.assertRaisesRegex(ValueError, "theater is required"):
            ReferenceManager(
                theater=None,  # type: ignore[arg-type]
                text_response_provider=self.provider,
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
        manager = ReferenceManager(
            self.theater,
            self.provider,
            self.notepad,
            self.story_state,
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
        manager = ReferenceManager(
            self.theater,
            self.provider,
            self.notepad,
            self.story_state,
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
            "components.reference_manager.get_quirk_generator_service",
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
        manager = ReferenceManager(self.theater, self.provider, self.notepad, self.story_state, self.image_provider, speech_provider)
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
            "components.reference_manager.get_quirk_generator_service",
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
            "components.reference_manager.get_quirk_generator_service",
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
            "components.reference_manager.get_quirk_generator_service",
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

    def test_reference_manager_binds_exact_reference_and_stable_voice(self) -> None:
        speech_provider = MagicMock(spec=SpeechProvider)
        speech_provider.select_voice.return_value = "voice_lyra"
        with tempfile.TemporaryDirectory() as ref_dir:
            ref_path = str(Path(ref_dir) / "lyra.png")
            Image.new("RGB", (10, 10), color="pink").save(ref_path)
            theater = MagicMock(spec=Theater)
            theater.characters_dir.return_value = Path("/nonexistent/base_characters")
            theater.updated_characters_dir.return_value = Path(ref_dir) / "references" / "characters"
            theater.references_dir.return_value = Path(ref_dir)
            manager = ReferenceManager(
                theater,
                self.provider, self.notepad, self.story_state, self.image_provider, speech_provider,
            )

            manager.create_or_update_character(
                "Lyra", personality="Curious", motivation="Learn", quirk="Hums",
                gender="female", image_reference="lyra",
            )
            # A later profile update does not silently replace either identity binding.
            manager.create_or_update_character("Lyra", personality="Brave", motivation="Learn", quirk="Hums", gender="female")

            character = manager.export_characters()[0]
            self.assertEqual(character["image_reference"], "output/artifacts/updated_references/characters/Lyra/1.png")
            self.assertEqual(Path(character.image_reference_path).read_bytes(), Path(ref_path).read_bytes())
            self.assertTrue(Path(character.image_reference_path).is_relative_to(theater.updated_characters_dir()))
            self.assertEqual(character["voice_id"], "voice_lyra")
            speech_provider.select_voice.assert_called_once()

    def test_reference_manager_generates_a_dedicated_portrait_without_image_tools(self) -> None:
        image = Image.new("RGB", (8, 8), "purple")
        payload = BytesIO()
        image.save(payload, "PNG")
        provider = MagicMock(spec=ImageProvider)
        provider.generate.return_value = ImageGenerationResult(
            image_bytes=payload.getvalue(), mime_type="image/png", provider="fast", model="portrait",
        )
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "Mira" / "1.png")
            theater = MagicMock(spec=Theater)
            theater.characters_dir.return_value = Path("/nonexistent/base_characters")
            theater.updated_characters_dir.return_value = Path(directory)
            theater.references_dir.return_value = Path(directory)
            manager = ReferenceManager(
                theater,
                self.provider, self.notepad, self.story_state, provider, self.speech_provider,
            )
            manager.create_or_update_character("Mira", personality="Alert", motivation="Help", quirk="Hums", gender="female")
            character = manager.export_characters()[0]
            self.assertEqual(character["image_reference_path"], output)
            self.assertTrue(Path(output).is_file())
        provider.generate.assert_called_once()

    def test_reference_manager_gating_and_iteration_chaining(self) -> None:
        image = Image.new("RGB", (8, 8), "purple")
        payload = BytesIO()
        image.save(payload, "PNG")
        provider = MagicMock(spec=ImageProvider)
        provider.generate.return_value = ImageGenerationResult(
            image_bytes=payload.getvalue(), mime_type="image/png", provider="fast", model="portrait",
        )
        with tempfile.TemporaryDirectory() as directory:
            iter1_output = str(Path(directory) / "Soran" / "1.png")
            iter2_output = str(Path(directory) / "Soran" / "2.png")

            theater = MagicMock(spec=Theater)
            theater.characters_dir.return_value = Path("/nonexistent/base_characters")
            theater.updated_characters_dir.return_value = Path(directory)
            theater.references_dir.return_value = Path(directory)
            manager = ReferenceManager(
                theater,
                self.provider, self.notepad, self.story_state, provider, self.speech_provider,
            )

            # 1. Initial creation generates iteration 1
            char1 = manager.create_or_update_character(
                "Soran", description="A young scout in leather tunic", personality="Brave", gender="male",
            )
            self.assertIsNotNone(char1)
            self.assertEqual(char1["image_reference_path"], iter1_output)
            self.assertTrue(Path(iter1_output).is_file())
            self.assertEqual(provider.generate.call_count, 1)

            # Verify call 1 had no reference images
            req1 = provider.generate.call_args_list[0][0][0]
            self.assertEqual(len(req1.references), 0)

            # 2. Updating personality/quirk without description change does NOT regenerate
            char2 = manager.create_or_update_character(
                "Soran", personality="Cautious and observant", quirk="Fidgets with compass",
            )
            self.assertIsNotNone(char2)
            self.assertEqual(char2["image_reference_path"], iter1_output)
            self.assertEqual(provider.generate.call_count, 1)  # No new generation!

            # 3. Updating with the identical description does NOT regenerate
            char3 = manager.create_or_update_character(
                "Soran", description="A young scout in leather tunic",
            )
            self.assertIsNotNone(char3)
            self.assertEqual(char3["image_reference_path"], iter1_output)
            self.assertEqual(provider.generate.call_count, 1)  # Still 1!

            # 4. Updating description DOES regenerate, producing iteration 2 with iteration 1 as reference
            char4 = manager.create_or_update_character(
                "Soran", description="Battle-hardened scout wearing spiked iron armor and a wolf cloak",
            )
            self.assertIsNotNone(char4)
            self.assertEqual(char4["image_reference_path"], iter2_output)
            self.assertTrue(Path(iter2_output).is_file())
            self.assertEqual(provider.generate.call_count, 2)

            # Verify call 2 had iteration 1 as reference
            req2 = provider.generate.call_args_list[1][0][0]
            self.assertEqual(len(req2.references), 1)
            self.assertEqual(req2.references[0].name, "1.png")
            self.assertEqual(req2.references[0].label, "Soran")

            # 5. Querying latest reference path returns iteration 2
            latest_path = manager.get_latest_reference_path_for_character("Soran")
            self.assertEqual(latest_path, iter2_output)
            # Querying by directory also returns iteration 2
            self.assertEqual(manager.get_latest_reference_path_for_character("references/Soran"), iter2_output)
            # Querying by iteration 1 also returns latest iteration 2
            self.assertEqual(manager.get_latest_reference_path_for_character("references/Soran/1.png"), iter2_output)

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

    def test_reference_manager_manages_player_character_and_binds_reference(self) -> None:
        with tempfile.TemporaryDirectory() as ref_dir:
            ref_path = str(Path(ref_dir) / "hero.png")
            Image.new("RGB", (10, 10), color="blue").save(ref_path)
            theater = MagicMock(spec=Theater)
            theater.characters_dir.return_value = Path("/nonexistent/base_characters")
            theater.updated_characters_dir.return_value = Path(ref_dir) / "references" / "characters"
            theater.references_dir.return_value = Path(ref_dir)
            manager = ReferenceManager(
                theater,
                self.provider, self.notepad, self.story_state, self.image_provider, self.speech_provider,
            )

            self.assertIsNone(manager.get_player_character())
            self.assertIsNone(manager.get_player_reference())

            player = manager.update_player_character(
                name="Valen",
                reference="hero",
                image_description="Tall knight in etched plate armor",
            )
            self.assertEqual(player.name, "Valen")
            self.assertEqual(player.reference, "output/artifacts/updated_references/characters/Valen/1.png")
            self.assertEqual(Path(player.reference_path).read_bytes(), Path(ref_path).read_bytes())
            self.assertTrue(Path(player.reference_path).is_relative_to(theater.updated_characters_dir()))
            self.assertEqual(player.reference_source, "existing")
            self.assertEqual(manager.get_player_reference(), player.reference)

            # Calling update again preserves bindings unless reference is updated
            updated = manager.update_player_character(image_description="Armor now tarnished")
            self.assertEqual(updated.image_description, "Armor now tarnished")
            self.assertEqual(updated.reference, player.reference)
            self.assertEqual(updated.reference_path, player.reference_path)

    def test_reference_manager_generates_player_portrait_when_missing(self) -> None:
        image = Image.new("RGB", (8, 8), "blue")
        payload = BytesIO()
        image.save(payload, "PNG")
        provider = MagicMock(spec=ImageProvider)
        provider.generate.return_value = ImageGenerationResult(
            image_bytes=payload.getvalue(), mime_type="image/png", provider="fast", model="portrait",
        )
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "Valen" / "1.png")
            theater = MagicMock(spec=Theater)
            theater.characters_dir.return_value = Path("/nonexistent/base_characters")
            theater.updated_characters_dir.return_value = Path(directory)
            theater.references_dir.return_value = Path(directory)
            manager = ReferenceManager(
                theater,
                self.provider, self.notepad, self.story_state, provider, self.speech_provider,
            )
            player = manager.update_player_character(
                name="Valen",
                image_description="Cloaked rogue with twin daggers",
            )
            self.assertEqual(player.reference_path, output)
            self.assertEqual(player.reference_source, "generated")
            self.assertTrue(Path(output).is_file())
        provider.generate.assert_called_once()

    def test_player_character_gating_and_iteration_chaining(self) -> None:
        image = Image.new("RGB", (8, 8), "purple")
        payload = BytesIO()
        image.save(payload, "PNG")
        provider = MagicMock(spec=ImageProvider)
        provider.generate.return_value = ImageGenerationResult(
            image_bytes=payload.getvalue(), mime_type="image/png", provider="fast", model="portrait",
        )
        with tempfile.TemporaryDirectory() as directory:
            iter1_output = str(Path(directory) / "Valen" / "1.png")
            iter2_output = str(Path(directory) / "Valen" / "2.png")

            theater = MagicMock(spec=Theater)
            theater.characters_dir.return_value = Path("/nonexistent/base_characters")
            theater.updated_characters_dir.return_value = Path(directory)
            theater.references_dir.return_value = Path(directory)
            manager = ReferenceManager(
                theater,
                self.provider, self.notepad, self.story_state, provider, self.speech_provider,
            )

            # 1. Initial creation generates iteration 1
            p1 = manager.update_player_character(
                name="Valen", image_description="A young scout in leather tunic",
            )
            self.assertEqual(p1.reference_path, iter1_output)
            self.assertTrue(Path(iter1_output).is_file())
            self.assertEqual(provider.generate.call_count, 1)

            # Verify call 1 had no reference images
            req1 = provider.generate.call_args_list[0][0][0]
            self.assertEqual(len(req1.references), 0)

            # 2. Updating name only without description change does NOT regenerate
            p2 = manager.update_player_character(name="Valen the Scout")
            self.assertEqual(p2.name, "Valen the Scout")
            self.assertEqual(p2.reference_path, iter1_output)
            self.assertEqual(provider.generate.call_count, 1)

            # 3. Updating with the identical description does NOT regenerate
            p3 = manager.update_player_character(image_description="A young scout in leather tunic")
            self.assertEqual(p3.reference_path, iter1_output)
            self.assertEqual(provider.generate.call_count, 1)

            # 4. Updating description DOES regenerate, producing iteration 2 with iteration 1 as reference
            p4 = manager.update_player_character(
                image_description="Battle-hardened scout wearing spiked iron armor and a wolf cloak",
            )
            self.assertEqual(p4.reference_path, iter2_output)
            self.assertTrue(Path(iter2_output).is_file())
            self.assertEqual(provider.generate.call_count, 2)

            # Verify call 2 had iteration 1 as reference
            req2 = provider.generate.call_args_list[1][0][0]
            self.assertEqual(len(req2.references), 1)
            self.assertEqual(req2.references[0].name, "1.png")
            self.assertEqual(req2.references[0].label, "Valen the Scout")

            # 5. Querying latest reference path returns iteration 2
            self.assertEqual(manager.get_latest_reference_path_for_character("Valen the Scout"), iter2_output)
            self.assertEqual(manager.get_latest_reference_path_for_character("player"), iter2_output)
            self.assertEqual(manager.get_latest_reference_path_for_character("references/Valen"), iter2_output)
            self.assertEqual(manager.get_latest_reference_path_for_character("references/Valen/1.png"), iter2_output)

    def test_player_character_export_and_import(self) -> None:
        manager = ReferenceManager(
            self.theater, self.provider, self.notepad, self.story_state, self.image_provider, self.speech_provider,
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

        manager2 = ReferenceManager(
            self.theater, self.provider, self.notepad, self.story_state, self.image_provider, self.speech_provider,
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
            "components.reference_manager.get_quirk_generator_service",
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
        manager = ReferenceManager(
            self.theater, self.provider, self.notepad, self.story_state, self.image_provider, self.speech_provider
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

    def test_reference_manager_resolves_existing_reference_for_character_name_slug(self) -> None:
        provider = MagicMock(spec=ImageProvider)
        with tempfile.TemporaryDirectory() as ref_dir:
            ref_path = str(Path(ref_dir) / "lady_lux.jpg")
            Image.new("RGB", (10, 10), color="yellow").save(ref_path)
            theater = MagicMock(spec=Theater)
            theater.characters_dir.return_value = Path("/nonexistent/base_characters")
            theater.updated_characters_dir.return_value = Path(ref_dir) / "references" / "characters"
            theater.references_dir.return_value = Path(ref_dir)
            manager = ReferenceManager(
                theater,
                self.provider, self.notepad, self.story_state, provider, self.speech_provider,
            )

            char = manager.create_or_update_character(
                "Lady Lux",
                personality="Glamorous",
                motivation="Freedom",
                quirk="Plays sax riffs",
                gender="female",
            )

            self.assertIsNotNone(char)
            self.assertEqual(char.image_reference, "output/artifacts/updated_references/characters/Lady_Lux/1.jpg")
            self.assertEqual(Path(char.image_reference_path).read_bytes(), Path(ref_path).read_bytes())
            self.assertTrue(Path(char.image_reference_path).is_relative_to(theater.updated_characters_dir()))
            self.assertEqual(char["image_reference_source"], "existing")
            provider.generate.assert_not_called()

    def test_reference_manager_prefers_base_reference_over_generated_character_png(self) -> None:
        provider = MagicMock(spec=ImageProvider)
        with tempfile.TemporaryDirectory() as ref_dir:
            gen_path = str(Path(ref_dir) / "Lady_Lux_character.png")
            ref_path = str(Path(ref_dir) / "lady_lux.jpg")
            Image.new("RGB", (10, 10), color="gray").save(gen_path)
            Image.new("RGB", (10, 10), color="yellow").save(ref_path)
            theater = MagicMock(spec=Theater)
            theater.characters_dir.return_value = Path("/nonexistent/base_characters")
            theater.updated_characters_dir.return_value = Path(ref_dir) / "references" / "characters"
            theater.references_dir.return_value = Path(ref_dir)
            manager = ReferenceManager(
                theater,
                self.provider, self.notepad, self.story_state, provider, self.speech_provider,
            )

            char = manager.create_or_update_character(
                "Lady Lux",
                personality="Glamorous",
                motivation="Freedom",
                quirk="Plays sax riffs",
                gender="female",
            )

            self.assertIsNotNone(char)
            self.assertEqual(char.image_reference, "output/artifacts/updated_references/characters/Lady_Lux/1.jpg")
            self.assertEqual(Path(char.image_reference_path).read_bytes(), Path(ref_path).read_bytes())
            self.assertTrue(Path(char.image_reference_path).is_relative_to(theater.updated_characters_dir()))
            self.assertEqual(char["image_reference_source"], "existing")
            provider.generate.assert_not_called()

    def test_reference_manager_resolves_player_character_by_slug(self) -> None:
        provider = MagicMock(spec=ImageProvider)
        with tempfile.TemporaryDirectory() as ref_dir:
            ref_path = str(Path(ref_dir) / "retro_pulsar.jpg")
            Image.new("RGB", (10, 10), color="blue").save(ref_path)
            theater = MagicMock(spec=Theater)
            theater.characters_dir.return_value = Path("/nonexistent/base_characters")
            theater.updated_characters_dir.return_value = Path(ref_dir) / "references" / "characters"
            theater.references_dir.return_value = Path(ref_dir)
            manager = ReferenceManager(
                theater,
                self.provider, self.notepad, self.story_state, provider, self.speech_provider,
            )

            player = manager.update_player_character(
                name="Retro Pulsar",
                image_description="Astronaut in 1960s suit",
            )

            self.assertEqual(player.name, "Retro Pulsar")
            self.assertEqual(player.reference, "output/artifacts/updated_references/characters/Retro_Pulsar/1.jpg")
            self.assertEqual(Path(player.reference_path).read_bytes(), Path(ref_path).read_bytes())
            self.assertTrue(Path(player.reference_path).is_relative_to(theater.updated_characters_dir()))
            self.assertEqual(player.reference_source, "existing")
            provider.generate.assert_not_called()


    def test_authored_characters_are_copied_and_tags_resolve_without_active_profiles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            theater = Theater(TheaterManager(directory), "stage")
            base = theater.characters_dir()
            (base / "Grim Vallos").mkdir(parents=True)
            (base / "Arthur Modella").mkdir()
            Image.new("RGB", (8, 8), "red").save(base / "Arthur Modella" / "1.png")
            Image.new("RGB", (8, 8), "blue").save(base / "Grim Vallos" / "1.png")
            Image.new("RGB", (8, 8), "green").save(base / "Grim Vallos" / "2.png")
            manager = ReferenceManager(theater, self.provider, story_state=self.story_state)

            references, error = resolve_provider_references(
                None, "<Arthur Modella> steps back from <Grim Vallos> as <Arthur Modella> waves.",
                reference_manager=manager,
            )

            self.assertIsNone(error)
            self.assertEqual([ref.label for ref in references], ["Arthur Modella", "Grim Vallos"])
            self.assertEqual(references[0].data, (base / "Arthur Modella" / "1.png").read_bytes())
            self.assertEqual(references[1].data, (base / "Grim Vallos" / "2.png").read_bytes())
            self.assertTrue((theater.updated_characters_dir() / "Grim_Vallos" / "1.png").is_file())
            self.assertEqual(manager.count(), 0)
            self.assertEqual(set(manager.available_character_images()), {"Arthur Modella", "Grim Vallos"})

    def test_character_updates_and_reinitialization_preserve_authored_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            theater = Theater(TheaterManager(directory), "stage")
            authored = theater.characters_dir() / "Arthur Modella" / "1.png"
            authored.parent.mkdir(parents=True)
            Image.new("RGB", (8, 8), "blue").save(authored)
            original = authored.read_bytes()
            payload = BytesIO()
            Image.new("RGB", (8, 8), "orange").save(payload, "PNG")
            provider = MagicMock(spec=ImageProvider)
            provider.generate.return_value = ImageGenerationResult(
                image_bytes=payload.getvalue(), mime_type="image/png", provider="fake", model="portrait",
            )
            manager = ReferenceManager(theater, self.provider, image_provider=provider)
            manager.create_or_update_character("Arthur Modella", description="A wizard", gender="male", personality="Wise", motivation="Learn", quirk="Hums")
            provider.generate.assert_not_called()
            manager.create_or_update_character("Arthur Modella", description="A wizard with orange hair")
            updated = theater.updated_characters_dir() / "Arthur_Modella" / "2.png"
            self.assertEqual(manager.get_character_visual_path("Arthur Modella"), str(updated))
            self.assertEqual(provider.generate.call_args.args[0].references[0].data, original)
            self.assertEqual(authored.read_bytes(), original)
            self.assertEqual(list(authored.parent.iterdir()), [authored])

            restored = ReferenceManager(theater, self.provider)
            self.assertEqual(restored.get_character_visual_path("Arthur Modella"), str(updated))
            references, error = resolve_provider_references(None, "<Arthur Modella> smiles.", reference_manager=restored)
            self.assertIsNone(error)
            self.assertEqual(references[0].data, updated.read_bytes())

    def test_loose_authored_portraits_are_not_discovered_or_bound(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            theater = Theater(TheaterManager(directory), "stage")
            theater.characters_dir().mkdir(parents=True)
            portrait = theater.characters_dir() / "Arthur Modella.png"
            Image.new("RGB", (8, 8), "blue").save(portrait)
            manager = ReferenceManager(theater, self.provider)

            self.assertEqual(manager.available_character_images(), {})
            self.assertIsNone(manager.get_character_visual_path("Arthur Modella"))
            self.assertIsNone(manager._reference_entry(str(portrait)))
            self.assertFalse(theater.updated_characters_dir().exists())
            references, error = resolve_provider_references(None, "<Arthur Modella> waves.", reference_manager=manager)
            self.assertEqual(references, [])
            self.assertIn("not found", error or "")
            with self.assertRaisesRegex(ValueError, "must be inside"):
                manager._session_reference_entry("Arthur Modella", portrait)

    def test_prompt_tags_do_not_match_partial_names_or_unmarked_characters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            theater = Theater(TheaterManager(directory), "stage")
            theater.characters_dir().mkdir(parents=True)
            for name in ("Ann", "Anna"):
                (theater.characters_dir() / name).mkdir()
                Image.new("RGB", (8, 8), "blue").save(theater.characters_dir() / name / "1.png")
            manager = ReferenceManager(theater, self.provider)
            references, error = resolve_provider_references(None, "<Anna> waves to Ann.", reference_manager=manager)
            self.assertIsNone(error)
            self.assertEqual([ref.label for ref in references], ["Anna"])
            for name in ("An", "Ann.png", "Missing"):
                references, error = resolve_provider_references(None, f"<{name}> waves.", reference_manager=manager)
                self.assertEqual(references, [])
                self.assertIn("not found", error or "")


if __name__ == "__main__":
    unittest.main()
