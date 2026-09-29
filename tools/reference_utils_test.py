"""Unit tests for shared reference resolution and character reference attachment utilities."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from typing import Optional
from unittest.mock import MagicMock

from components.character_manager import (
    Character,
    CharacterLookupResult,
    CharacterManager,
    PlayerCharacter,
)
from tools.reference_utils import (
    get_reference_label,
    is_character_reference,
    resolve_provider_references,
)


class DummyPathResolver:
    """Test helper implementing ImagePathResolver protocol without mocks."""

    def __init__(self, mapping: Optional[dict[str, str]] = None) -> None:
        self.mapping: dict[str, str] = mapping if mapping is not None else {}

    def resolve_image_path(self, query: str) -> Optional[str]:
        return self.mapping.get(query.strip())


class TestReferenceUtils(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_dummy_image_file(self, filename: str, content: bytes = b"dummy_image_data") -> str:
        filepath = os.path.join(self.temp_dir, filename)
        with open(filepath, "wb") as f:
            f.write(content)
        return filepath

    # --- is_character_reference tests ---

    def test_is_character_reference_returns_false_when_manager_none(self) -> None:
        self.assertFalse(is_character_reference("hero", character_manager=None))

    def test_is_character_reference_returns_false_for_empty_ref(self) -> None:
        mock_mgr = MagicMock(spec=CharacterManager)
        self.assertFalse(is_character_reference("", character_manager=mock_mgr))
        self.assertFalse(is_character_reference("   ", character_manager=mock_mgr))

    def test_is_character_reference_matches_lookup_result_character_name(self) -> None:
        mock_mgr = MagicMock(spec=CharacterManager)
        char = Character(name="Aria", gender="female", alias="The Bard", image_reference="aria_ref")
        lookup = CharacterLookupResult(characters=[char])

        self.assertTrue(is_character_reference("Aria", character_manager=mock_mgr, lookup_result=lookup))
        self.assertTrue(is_character_reference("aria", character_manager=mock_mgr, lookup_result=lookup))
        self.assertTrue(is_character_reference("The Bard", character_manager=mock_mgr, lookup_result=lookup))
        self.assertTrue(is_character_reference("aria_ref", character_manager=mock_mgr, lookup_result=lookup))

    def test_is_character_reference_matches_lookup_result_character_stem(self) -> None:
        mock_mgr = MagicMock(spec=CharacterManager)
        char = Character(name="Kael", gender="male", image_reference="/images/kael_portrait.png")
        lookup = CharacterLookupResult(characters=[char])

        self.assertTrue(is_character_reference("kael_portrait", character_manager=mock_mgr, lookup_result=lookup))
        self.assertTrue(is_character_reference("kael_portrait.png", character_manager=mock_mgr, lookup_result=lookup))

    def test_is_character_reference_matches_lookup_result_character_word_boundary(self) -> None:
        mock_mgr = MagicMock(spec=CharacterManager)
        char = Character(name="Cedric", gender="male")
        lookup = CharacterLookupResult(characters=[char])

        self.assertTrue(is_character_reference("cedric_standing", character_manager=mock_mgr, lookup_result=lookup))
        self.assertTrue(is_character_reference("sir-cedric-armor", character_manager=mock_mgr, lookup_result=lookup))
        self.assertFalse(is_character_reference("cedricus", character_manager=mock_mgr, lookup_result=lookup))

    def test_is_character_reference_matches_lookup_result_player(self) -> None:
        mock_mgr = MagicMock(spec=CharacterManager)
        player = PlayerCharacter(name="Valen", reference="valen_hero.png", reference_path="/path/to/valen_hero.png")
        lookup = CharacterLookupResult(player=player)

        self.assertTrue(is_character_reference("Valen", character_manager=mock_mgr, lookup_result=lookup))
        self.assertTrue(is_character_reference("valen_hero", character_manager=mock_mgr, lookup_result=lookup))
        self.assertTrue(is_character_reference("/path/to/valen_hero.png", character_manager=mock_mgr, lookup_result=lookup))
        self.assertTrue(is_character_reference("valen_running", character_manager=mock_mgr, lookup_result=lookup))

    def test_is_character_reference_matches_character_manager_get_character_references(self) -> None:
        mock_mgr = MagicMock(spec=CharacterManager)
        elena_file = os.path.abspath(self._create_dummy_image_file("elena.png"))
        mock_mgr.get_character_references.return_value = ["morgan_v1", elena_file]

        self.assertTrue(is_character_reference("morgan_v1", character_manager=mock_mgr))
        self.assertTrue(is_character_reference("morgan_v1.png", character_manager=mock_mgr))
        self.assertTrue(is_character_reference("elena", character_manager=mock_mgr))
        self.assertTrue(
            is_character_reference(
                "custom_query",
                character_manager=mock_mgr,
                resolved_path=elena_file,
            )
        )

    def test_is_character_reference_matches_filename_tags(self) -> None:
        mock_mgr = MagicMock(spec=CharacterManager)
        mock_mgr.get_character_references.return_value = []

        self.assertTrue(is_character_reference("hero_character.png", character_manager=mock_mgr))
        self.assertTrue(is_character_reference("scene_player_character_1", character_manager=mock_mgr))
        self.assertTrue(is_character_reference("npc_portrait.jpg", character_manager=mock_mgr))
        self.assertTrue(is_character_reference("character_concept", character_manager=mock_mgr))

    def test_is_character_reference_returns_false_for_non_character(self) -> None:
        mock_mgr = MagicMock(spec=CharacterManager)
        mock_mgr.get_character_references.return_value = ["hero"]
        lookup = CharacterLookupResult(characters=[Character(name="hero", gender="nonbinary")])

        self.assertFalse(is_character_reference("castle_courtyard", character_manager=mock_mgr, lookup_result=lookup))
        self.assertFalse(is_character_reference("forest_path_day.png", character_manager=mock_mgr, lookup_result=lookup))
        self.assertFalse(is_character_reference("magic_sword", character_manager=mock_mgr, lookup_result=lookup))

    # --- resolve_provider_references tests ---

    def test_resolve_provider_references_empty_inputs(self) -> None:
        refs, err = resolve_provider_references(None, prompt="", character_manager=None, visual=None)
        self.assertEqual(refs, [])
        self.assertIsNone(err)

    def test_resolve_provider_references_from_character_manager_prompt(self) -> None:
        hero_file = self._create_dummy_image_file("hero.png", b"hero_png_data")
        resolver = DummyPathResolver({"hero": hero_file})

        mock_mgr = MagicMock(spec=CharacterManager)
        mock_mgr.lookup_character.return_value = CharacterLookupResult(
            characters=[Character(name="Hero", gender="nonbinary", image_reference="hero")]
        )

        refs, err = resolve_provider_references(
            reference_images=None,
            prompt="Hero walking across a meadow",
            character_manager=mock_mgr,
            visual=resolver,
        )

        self.assertIsNone(err)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].name, "hero.png")
        self.assertEqual(refs[0].data, b"hero_png_data")
        self.assertEqual(refs[0].mime_type, "image/png")

    def test_resolve_provider_references_handles_different_image_mime_types(self) -> None:
        webp_file = self._create_dummy_image_file("hero.webp", b"webp_data")
        jpeg_file = self._create_dummy_image_file("villain.jpg", b"jpeg_data")
        resolver = DummyPathResolver({"hero": webp_file, "villain": jpeg_file})

        mock_mgr = MagicMock(spec=CharacterManager)
        mock_mgr.lookup_character.return_value = CharacterLookupResult(characters=[])

        refs, err = resolve_provider_references(
            reference_images=["hero", "villain"],
            prompt="Action scene",
            character_manager=mock_mgr,
            visual=resolver,
        )

        self.assertIsNone(err)
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0].mime_type, "image/webp")
        self.assertEqual(refs[1].mime_type, "image/jpeg")

    def test_resolve_provider_references_caller_string_split(self) -> None:
        img1 = self._create_dummy_image_file("tree.png", b"tree_data")
        img2 = self._create_dummy_image_file("rock.png", b"rock_data")
        resolver = DummyPathResolver({"tree": img1, "rock": img2})

        refs, err = resolve_provider_references(
            reference_images="tree, rock",
            prompt="Nature landscape",
            character_manager=None,
            visual=resolver,
        )

        self.assertIsNone(err)
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0].name, "tree.png")
        self.assertEqual(refs[1].name, "rock.png")

    def test_resolve_provider_references_missing_reference_returns_error(self) -> None:
        resolver = DummyPathResolver({})

        refs, err = resolve_provider_references(
            reference_images=["non_existent_ref"],
            prompt="Some scene",
            character_manager=None,
            visual=resolver,
        )

        self.assertEqual(refs, [])
        self.assertEqual(err, "Error: Reference image 'non_existent_ref' not found.")

    def test_resolve_provider_references_missing_character_ref_overridden_by_char_manager(self) -> None:
        hero_file = self._create_dummy_image_file("hero.png", b"hero_data")
        resolver = DummyPathResolver({"hero": hero_file})

        mock_mgr = MagicMock(spec=CharacterManager)
        char = Character(name="hero", gender="nonbinary", image_reference="hero")
        mock_mgr.lookup_character.return_value = CharacterLookupResult(characters=[char])
        mock_mgr.get_character_references.return_value = ["hero"]

        # Caller specifies "hero" which doesn't resolve by name "hero_portrait" in resolver,
        # but prompt resolved "hero", and "hero_portrait" is identified as a character ref.
        refs, err = resolve_provider_references(
            reference_images=["hero_portrait"],
            prompt="hero walks into the woods",
            character_manager=mock_mgr,
            visual=resolver,
        )

        self.assertIsNone(err)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].name, "hero.png")

    def test_resolve_provider_references_character_manager_overrides_caller_character(self) -> None:
        hero_file = self._create_dummy_image_file("hero.png", b"hero_bytes")
        villain_file = self._create_dummy_image_file("villain.png", b"villain_bytes")
        resolver = DummyPathResolver({"hero": hero_file, "villain": villain_file})

        mock_mgr = MagicMock(spec=CharacterManager)
        mock_mgr.lookup_character.return_value = CharacterLookupResult(
            characters=[Character(name="hero", gender="nonbinary", image_reference="hero")]
        )
        mock_mgr.get_character_references.return_value = ["hero", "villain"]

        # Prompt matches "hero". Caller specifies "villain". "villain" is a character ref,
        # so it gets overridden by character_manager's match.
        refs, err = resolve_provider_references(
            reference_images="villain",
            prompt="hero in a forest",
            character_manager=mock_mgr,
            visual=resolver,
        )

        self.assertIsNone(err)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].name, "hero.png")

    def test_resolve_provider_references_preserves_location_reference(self) -> None:
        hero_file = self._create_dummy_image_file("hero.png", b"hero_bytes")
        castle_file = self._create_dummy_image_file("castle_courtyard.png", b"castle_bytes")
        resolver = DummyPathResolver({"hero": hero_file, "castle_courtyard": castle_file})

        mock_mgr = MagicMock(spec=CharacterManager)
        mock_mgr.lookup_character.return_value = CharacterLookupResult(
            characters=[Character(name="hero", gender="nonbinary", image_reference="hero")]
        )
        mock_mgr.get_character_references.return_value = ["hero"]

        # Prompt matches "hero". Caller specifies ["hero", "castle_courtyard"].
        # "hero" is deduplicated/overridden, "castle_courtyard" is preserved.
        refs, err = resolve_provider_references(
            reference_images=["hero", "castle_courtyard"],
            prompt="hero at the castle courtyard",
            character_manager=mock_mgr,
            visual=resolver,
        )

        self.assertIsNone(err)
        self.assertEqual(len(refs), 2)
        ref_names = [r.name for r in refs]
        self.assertIn("hero.png", ref_names)
        self.assertIn("castle_courtyard.png", ref_names)

    def test_resolve_provider_references_deduplicates_by_path(self) -> None:
        hero_file = self._create_dummy_image_file("hero.png", b"hero_bytes")
        resolver = DummyPathResolver({"hero": hero_file, "hero_alias": hero_file})

        mock_mgr = MagicMock(spec=CharacterManager)
        mock_mgr.lookup_character.return_value = CharacterLookupResult(
            characters=[Character(name="hero", gender="nonbinary", image_reference="hero")]
        )
        mock_mgr.get_character_references.return_value = ["hero"]

        refs, err = resolve_provider_references(
            reference_images=["hero_alias"],
            prompt="hero in tavern",
            character_manager=mock_mgr,
            visual=resolver,
        )

        self.assertIsNone(err)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].name, "hero.png")

    def test_resolve_provider_references_handles_unreadable_file(self) -> None:
        bad_path = os.path.join(self.temp_dir, "non_existent_folder", "ghost.png")
        resolver = DummyPathResolver({"ghost": bad_path})

        refs, err = resolve_provider_references(
            reference_images=["ghost"],
            prompt="scene",
            character_manager=None,
            visual=resolver,
        )

        self.assertEqual(refs, [])
        self.assertIsNotNone(err)
        assert err is not None
        self.assertTrue(err.startswith("Error loading reference image 'ghost':"))

    def test_get_reference_label_matches_player_and_characters(self) -> None:
        lookup = CharacterLookupResult(
            characters=[
                Character(name="Captain Nova", gender="female", image_reference="nova_ref"),
                Character(name="Dr. Aris", gender="male", image_reference="aris_ref"),
            ],
            player=PlayerCharacter(name="Elena", reference="elena_ref"),
        )
        self.assertEqual(get_reference_label("elena_ref", lookup_result=lookup), "Elena")
        self.assertEqual(get_reference_label("nova_ref", lookup_result=lookup), "Captain Nova")
        self.assertEqual(get_reference_label("aris_ref", lookup_result=lookup), "Dr. Aris")
        self.assertEqual(get_reference_label("forest_clearing.png", lookup_result=lookup), "forest_clearing")

    def test_resolve_provider_references_assigns_character_labels(self) -> None:
        nova_file = self._create_dummy_image_file("c_nova.png", b"nova_bytes")
        aris_file = self._create_dummy_image_file("aris_doc.png", b"aris_bytes")
        forest_file = self._create_dummy_image_file("ancient_ruins.png", b"ruin_bytes")
        resolver = DummyPathResolver({
            "nova_ref": nova_file,
            "aris_ref": aris_file,
            "ancient_ruins": forest_file,
        })

        mock_mgr = MagicMock(spec=CharacterManager)
        mock_mgr.lookup_character.return_value = CharacterLookupResult(
            characters=[
                Character(name="Captain Nova", gender="female", image_reference="nova_ref"),
                Character(name="Dr. Aris", gender="male", image_reference="aris_ref"),
            ]
        )

        refs, err = resolve_provider_references(
            reference_images=["ancient_ruins"],
            prompt="Captain Nova and Dr. Aris exploring ancient ruins",
            character_manager=mock_mgr,
            visual=resolver,
        )

        self.assertIsNone(err)
        self.assertEqual(len(refs), 3)
        # Check that character references have their character names as labels
        labels = {r.name: r.label for r in refs}
        self.assertEqual(labels["c_nova.png"], "Captain Nova")
        self.assertEqual(labels["aris_doc.png"], "Dr. Aris")
        self.assertEqual(labels["ancient_ruins.png"], "ancient_ruins")


if __name__ == "__main__":
    unittest.main()
