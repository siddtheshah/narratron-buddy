"""Unit tests for manager-owned reference resolution and image attachment."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Optional

from components.reference_manager import (
    Character,
    CharacterLookupResult,
    PlayerCharacter,
)
from components.theater_manager import TheaterManager
from testing.reference_manager_fixture import make_reference_manager


class DummyPathResolver:
    """Test helper implementing ImagePathResolver protocol without mocks."""

    def __init__(self, mapping: Optional[dict[str, str]] = None) -> None:
        self.mapping: dict[str, str] = mapping if mapping is not None else {}

    def resolve_image_path(self, query: str) -> Optional[str]:
        return self.mapping.get(query.strip())


class TestReferenceManagerResolution(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.theater = TheaterManager(base_theaters_dir=self.temp_dir).theater("resolution")
        self.manager = make_reference_manager(self.theater)

    def tearDown(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_canvas_stamp_cannot_be_attached_as_generation_reference(self) -> None:
        stamp_dir = os.path.join(self.temp_dir, "stamps")
        os.makedirs(stamp_dir)
        stamp_path = self._create_dummy_image_file("stamps/shovel.png")
        resolver = DummyPathResolver({"shovel": stamp_path})
        manager = self.manager
        references, error = manager.resolve_provider_references(["shovel"], visual=resolver)
        self.assertEqual(references, [])
        self.assertIn("canvas token", error or "")

    def _create_dummy_image_file(self, filename: str, content: bytes = b"dummy_image_data") -> str:
        filepath = os.path.join(self.temp_dir, filename)
        with open(filepath, "wb") as f:
            f.write(content)
        return filepath

    # --- is_character_reference tests ---

    def test_is_character_reference_returns_false_for_empty_ref(self) -> None:
        manager = self.manager
        self.assertFalse(manager.is_character_reference(""))
        self.assertFalse(manager.is_character_reference("   "))

    def test_is_character_reference_matches_lookup_result_character_name(self) -> None:
        manager = self.manager
        char = Character(name="Aria", gender="female", alias="The Bard", image_reference="aria_ref")
        lookup = CharacterLookupResult(characters=[char])

        self.assertTrue(manager.is_character_reference("Aria", lookup_result=lookup))
        self.assertTrue(manager.is_character_reference("aria", lookup_result=lookup))
        self.assertTrue(manager.is_character_reference("The Bard", lookup_result=lookup))
        self.assertTrue(manager.is_character_reference("aria_ref", lookup_result=lookup))

    def test_is_character_reference_matches_lookup_result_character_stem(self) -> None:
        manager = self.manager
        char = Character(name="Kael", gender="male", image_reference="/images/kael_portrait.png")
        lookup = CharacterLookupResult(characters=[char])

        self.assertTrue(manager.is_character_reference("kael_portrait", lookup_result=lookup))
        self.assertTrue(manager.is_character_reference("kael_portrait.png", lookup_result=lookup))

    def test_is_character_reference_matches_lookup_result_character_word_boundary(self) -> None:
        manager = self.manager
        char = Character(name="Cedric", gender="male")
        lookup = CharacterLookupResult(characters=[char])

        self.assertTrue(manager.is_character_reference("cedric_standing", lookup_result=lookup))
        self.assertTrue(manager.is_character_reference("sir-cedric-armor", lookup_result=lookup))
        self.assertFalse(manager.is_character_reference("cedricus", lookup_result=lookup))

    def test_is_character_reference_matches_lookup_result_player(self) -> None:
        manager = self.manager
        player = PlayerCharacter(name="Valen", reference="valen_hero.png", reference_path="/path/to/valen_hero.png")
        lookup = CharacterLookupResult(player=player)

        self.assertTrue(manager.is_character_reference("Valen", lookup_result=lookup))
        self.assertTrue(manager.is_character_reference("valen_hero", lookup_result=lookup))
        self.assertTrue(manager.is_character_reference("/path/to/valen_hero.png", lookup_result=lookup))
        self.assertTrue(manager.is_character_reference("valen_running", lookup_result=lookup))

    def test_is_character_reference_matches_reference_manager_get_character_references(self) -> None:
        manager = self.manager
        elena_file = os.path.abspath(self._create_dummy_image_file("elena.png"))
        manager.get_character_references.return_value = ["morgan_v1", elena_file]

        self.assertTrue(manager.is_character_reference("morgan_v1"))
        self.assertTrue(manager.is_character_reference("morgan_v1.png"))
        self.assertTrue(manager.is_character_reference("elena"))
        self.assertTrue(
            manager.is_character_reference(
                "custom_query",
                resolved_path=elena_file,
            )
        )

    def test_is_character_reference_matches_filename_tags(self) -> None:
        manager = self.manager
        manager.get_character_references.return_value = []

        self.assertTrue(manager.is_character_reference("hero_character.png"))
        self.assertTrue(manager.is_character_reference("scene_player_character_1"))
        self.assertTrue(manager.is_character_reference("npc_portrait.jpg"))
        self.assertTrue(manager.is_character_reference("character_concept"))

    def test_is_character_reference_returns_false_for_non_character(self) -> None:
        manager = self.manager
        manager.get_character_references.return_value = ["hero"]
        lookup = CharacterLookupResult(characters=[Character(name="hero", gender="nonbinary")])

        self.assertFalse(manager.is_character_reference("castle_courtyard", lookup_result=lookup))
        self.assertFalse(manager.is_character_reference("forest_path_day.png", lookup_result=lookup))
        self.assertFalse(manager.is_character_reference("magic_sword", lookup_result=lookup))

    # --- resolve_provider_references tests ---

    def test_resolve_provider_references_empty_inputs(self) -> None:
        manager = self.manager
        refs, err = manager.resolve_provider_references(None, prompt="", visual=None)
        self.assertEqual(refs, [])
        self.assertIsNone(err)

    def test_resolve_provider_references_from_reference_manager_prompt(self) -> None:
        hero_file = self._create_dummy_image_file("hero.png", b"hero_png_data")
        resolver = DummyPathResolver({"hero": hero_file})

        manager = self.manager
        manager.lookup_character.return_value = CharacterLookupResult(
            characters=[Character(name="Hero", gender="nonbinary", image_reference="hero")]
        )

        refs, err = manager.resolve_provider_references(
            reference_images=None,
            prompt="Hero walking across a meadow",
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

        manager = self.manager
        manager.lookup_character.return_value = CharacterLookupResult(characters=[])

        refs, err = manager.resolve_provider_references(
            reference_images=["hero", "villain"],
            prompt="Action scene",
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
        manager = self.manager

        refs, err = manager.resolve_provider_references(
            reference_images="tree, rock",
            prompt="Nature landscape",
            visual=resolver,
        )

        self.assertIsNone(err)
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0].name, "tree.png")
        self.assertEqual(refs[1].name, "rock.png")

    def test_resolve_provider_references_missing_reference_returns_error(self) -> None:
        resolver = DummyPathResolver({})
        manager = self.manager

        refs, err = manager.resolve_provider_references(
            reference_images=["non_existent_ref"],
            prompt="Some scene",
            visual=resolver,
        )

        self.assertEqual(refs, [])
        self.assertEqual(err, "Error: Reference image 'non_existent_ref' not found.")

    def test_resolve_provider_references_missing_character_ref_overridden_by_char_manager(self) -> None:
        hero_file = self._create_dummy_image_file("hero.png", b"hero_data")
        resolver = DummyPathResolver({"hero": hero_file})

        manager = self.manager
        char = Character(name="hero", gender="nonbinary", image_reference="hero")
        manager.lookup_character.return_value = CharacterLookupResult(characters=[char])
        manager.get_character_references.return_value = ["hero"]

        # Caller specifies "hero" which doesn't resolve by name "hero_portrait" in resolver,
        # but prompt resolved "hero", and "hero_portrait" is identified as a character ref.
        refs, err = manager.resolve_provider_references(
            reference_images=["hero_portrait"],
            prompt="hero walks into the woods",
            visual=resolver,
        )

        self.assertIsNone(err)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].name, "hero.png")

    def test_resolve_provider_references_reference_manager_overrides_caller_character(self) -> None:
        hero_file = self._create_dummy_image_file("hero.png", b"hero_bytes")
        villain_file = self._create_dummy_image_file("villain.png", b"villain_bytes")
        resolver = DummyPathResolver({"hero": hero_file, "villain": villain_file})

        manager = self.manager
        manager.lookup_character.return_value = CharacterLookupResult(
            characters=[Character(name="hero", gender="nonbinary", image_reference="hero")]
        )
        manager.get_character_references.return_value = ["hero", "villain"]

        # Prompt matches "hero". Caller specifies "villain". "villain" is a character ref,
        # so it gets overridden by reference_manager's match.
        refs, err = manager.resolve_provider_references(
            reference_images="villain",
            prompt="hero in a forest",
            visual=resolver,
        )

        self.assertIsNone(err)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].name, "hero.png")

    def test_resolve_provider_references_preserves_location_reference(self) -> None:
        hero_file = self._create_dummy_image_file("hero.png", b"hero_bytes")
        castle_file = self._create_dummy_image_file("castle_courtyard.png", b"castle_bytes")
        resolver = DummyPathResolver({"hero": hero_file, "castle_courtyard": castle_file})

        manager = self.manager
        manager.lookup_character.return_value = CharacterLookupResult(
            characters=[Character(name="hero", gender="nonbinary", image_reference="hero")]
        )
        manager.get_character_references.return_value = ["hero"]

        # Prompt matches "hero". Caller specifies ["hero", "castle_courtyard"].
        # "hero" is deduplicated/overridden, "castle_courtyard" is preserved.
        refs, err = manager.resolve_provider_references(
            reference_images=["hero", "castle_courtyard"],
            prompt="hero at the castle courtyard",
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

        manager = self.manager
        manager.lookup_character.return_value = CharacterLookupResult(
            characters=[Character(name="hero", gender="nonbinary", image_reference="hero")]
        )
        manager.get_character_references.return_value = ["hero"]

        refs, err = manager.resolve_provider_references(
            reference_images=["hero_alias"],
            prompt="hero in tavern",
            visual=resolver,
        )

        self.assertIsNone(err)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].name, "hero.png")

    def test_resolve_provider_references_handles_unreadable_file(self) -> None:
        bad_path = os.path.join(self.temp_dir, "non_existent_folder", "ghost.png")
        resolver = DummyPathResolver({"ghost": bad_path})
        manager = self.manager

        refs, err = manager.resolve_provider_references(
            reference_images=["ghost"],
            prompt="scene",
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
        self.assertEqual(self.manager.get_reference_label("elena_ref", lookup_result=lookup), "Elena")
        self.assertEqual(self.manager.get_reference_label("nova_ref", lookup_result=lookup), "Captain Nova")
        self.assertEqual(self.manager.get_reference_label("aris_ref", lookup_result=lookup), "Dr. Aris")
        self.assertEqual(self.manager.get_reference_label("forest_clearing.png", lookup_result=lookup), "forest_clearing")

    def test_resolve_provider_references_assigns_character_labels(self) -> None:
        nova_file = self._create_dummy_image_file("c_nova.png", b"nova_bytes")
        aris_file = self._create_dummy_image_file("aris_doc.png", b"aris_bytes")
        forest_file = self._create_dummy_image_file("ancient_ruins.png", b"ruin_bytes")
        resolver = DummyPathResolver({
            "nova_ref": nova_file,
            "aris_ref": aris_file,
            "ancient_ruins": forest_file,
        })

        manager = self.manager
        manager.lookup_character.return_value = CharacterLookupResult(
            characters=[
                Character(name="Captain Nova", gender="female", image_reference="nova_ref"),
                Character(name="Dr. Aris", gender="male", image_reference="aris_ref"),
            ]
        )

        refs, err = manager.resolve_provider_references(
            reference_images=["ancient_ruins"],
            prompt="Captain Nova and Dr. Aris exploring ancient ruins",
            visual=resolver,
        )

        self.assertIsNone(err)
        self.assertEqual(len(refs), 3)
        # Check that character references have their character names as labels
        labels = {r.name: r.label for r in refs}
        self.assertEqual(labels["c_nova.png"], "Captain Nova")
        self.assertEqual(labels["aris_doc.png"], "Dr. Aris")
        self.assertEqual(labels["ancient_ruins.png"], "ancient_ruins")

    def test_resolve_provider_references_resolves_character_name_to_latest_iteration(self) -> None:
        char_dir = os.path.join(self.temp_dir, "Soran")
        os.makedirs(char_dir, exist_ok=True)
        iter1 = os.path.join(char_dir, "1.png")
        iter2 = os.path.join(char_dir, "2.png")
        with open(iter1, "wb") as f:
            f.write(b"iter1_bytes")
        with open(iter2, "wb") as f:
            f.write(b"iter2_bytes")

        manager = self.manager
        manager.lookup_character.return_value = CharacterLookupResult(characters=[])
        manager.get_character_references.return_value = []
        manager.get_latest_reference_path_for_character.side_effect = lambda query: (
            iter2 if "soran" in str(query).lower() else None
        )

        resolver = DummyPathResolver({})

        # Caller provides character name
        refs, err = manager.resolve_provider_references(
            reference_images=["Soran"],
            prompt="A scenic forest view",
            visual=resolver,
        )
        self.assertIsNone(err)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].name, "2.png")
        self.assertEqual(refs[0].data, b"iter2_bytes")
        self.assertEqual(refs[0].label, "Soran")

    def test_resolve_provider_references_older_iteration_resolves_to_latest_iteration(self) -> None:
        char_dir = os.path.join(self.temp_dir, "Soran")
        os.makedirs(char_dir, exist_ok=True)
        iter1 = os.path.join(char_dir, "1.png")
        iter2 = os.path.join(char_dir, "2.png")
        with open(iter1, "wb") as f:
            f.write(b"iter1_bytes")
        with open(iter2, "wb") as f:
            f.write(b"iter2_bytes")

        manager = self.manager
        manager.lookup_character.return_value = CharacterLookupResult(characters=[])
        manager.get_character_references.return_value = []
        manager.get_latest_reference_path_for_character.side_effect = lambda query: (
            iter2 if "soran" in str(query).lower() else None
        )

        resolver = DummyPathResolver({})

        # Caller explicitly provided older iteration path "references/Soran/1.png"
        refs, err = manager.resolve_provider_references(
            reference_images=["references/Soran/1.png"],
            prompt="Action scene",
            visual=resolver,
        )
        self.assertIsNone(err)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].name, "2.png")
        self.assertEqual(refs[0].data, b"iter2_bytes")
        self.assertEqual(refs[0].label, "Soran")

    def test_get_reference_label_character_folder_iteration(self) -> None:
        lookup = CharacterLookupResult(
            characters=[
                Character(
                    name="Soran",
                    gender="male",
                    image_reference="references/Soran/2.png",
                    image_reference_path="/theaters/references/Soran/2.png",
                )
            ]
        )
        label = self.manager.get_reference_label(
            ref="references/Soran/2.png",
            resolved_path="/theaters/references/Soran/2.png",
            lookup_result=lookup,
        )
        self.assertEqual(label, "Soran")

    def test_resolve_provider_references_player_resolves_to_latest_iteration(self) -> None:
        char_dir = os.path.join(self.temp_dir, "Valen")
        os.makedirs(char_dir, exist_ok=True)
        iter1 = os.path.join(char_dir, "1.png")
        iter2 = os.path.join(char_dir, "2.png")
        with open(iter1, "wb") as f:
            f.write(b"player_iter1_bytes")
        with open(iter2, "wb") as f:
            f.write(b"player_iter2_bytes")

        manager = self.manager
        player = PlayerCharacter(name="Valen", reference="references/Valen/2.png", reference_path=iter2)
        manager.lookup_character.return_value = CharacterLookupResult(characters=[], player=player)
        manager.get_character_references.return_value = []
        manager.get_latest_reference_path_for_character.side_effect = lambda query: (
            iter2 if any(k in str(query).lower() for k in ("valen", "player")) else None
        )

        resolver = DummyPathResolver({})

        # Caller provides "player", should resolve to latest iteration iter2
        refs, err = manager.resolve_provider_references(
            reference_images=["player"],
            prompt="Dramatic battle",
            visual=resolver,
        )
        self.assertIsNone(err)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].name, "2.png")
        self.assertEqual(refs[0].data, b"player_iter2_bytes")
        self.assertEqual(refs[0].label, "Valen")

    def test_add_and_get_canvas_capture(self) -> None:
        captures_dir = os.path.join(self.temp_dir, "canvas_captures")
        os.makedirs(captures_dir, exist_ok=True)
        cap1 = os.path.join(captures_dir, "canvas_abc12345.png")
        Path(cap1).write_bytes(b"capture_data_1")

        cap2 = os.path.join(captures_dir, "canvas_def67890.jpg")
        Path(cap2).write_bytes(b"capture_data_2")

        handle1 = self.manager.add_canvas_capture(cap1)
        self.assertEqual(handle1, "canvas_abc12345")
        self.assertEqual(self.manager.get_canvas_capture_path(handle1), str(Path(cap1).resolve()))
        self.assertEqual(self.manager.get_canvas_capture_path(f"<{handle1}>"), str(Path(cap1).resolve()))

        handle2 = self.manager.add_canvas_capture(cap2, handle="custom_capture")
        self.assertEqual(handle2, "custom_capture")
        self.assertEqual(self.manager.get_canvas_capture_path("custom_capture"), str(Path(cap2).resolve()))
        self.assertEqual(self.manager.get_canvas_capture_path("canvas_capture"), str(Path(cap2).resolve()))

        available = self.manager.available_canvas_captures()
        self.assertIn("custom_capture", available)
        self.assertIn("canvas_abc12345", available)

    def test_resolve_provider_references_with_tagged_canvas_capture(self) -> None:
        captures_dir = os.path.join(self.temp_dir, "canvas_captures")
        os.makedirs(captures_dir, exist_ok=True)
        cap_file = os.path.join(captures_dir, "canvas_testcap.png")
        Path(cap_file).write_bytes(b"test_capture_bytes")

        handle = self.manager.add_canvas_capture(cap_file)
        resolver = DummyPathResolver({})

        refs, err = self.manager.resolve_provider_references(
            prompt=f"A painting based on <{handle}>",
            visual=resolver,
        )
        self.assertIsNone(err)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].name, "canvas_testcap.png")
        self.assertEqual(refs[0].data, b"test_capture_bytes")
        self.assertEqual(refs[0].label, handle)
        self.assertEqual(refs[0].mime_type, "image/png")

    def test_resolve_provider_references_with_prompt_mentioned_canvas_capture(self) -> None:
        captures_dir = os.path.join(self.temp_dir, "canvas_captures")
        os.makedirs(captures_dir, exist_ok=True)
        cap_file = os.path.join(captures_dir, "canvas_untagged.webp")
        Path(cap_file).write_bytes(b"untagged_bytes")

        handle = self.manager.add_canvas_capture(cap_file)
        resolver = DummyPathResolver({})

        refs, err = self.manager.resolve_provider_references(
            prompt=f"Draw something drawing from {handle} right now",
            visual=resolver,
        )
        self.assertIsNone(err)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].name, "canvas_untagged.webp")
        self.assertEqual(refs[0].data, b"untagged_bytes")
        self.assertEqual(refs[0].mime_type, "image/webp")


if __name__ == "__main__":
    unittest.main()
