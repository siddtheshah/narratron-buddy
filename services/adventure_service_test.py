"""Unit tests for services/adventure_service.py."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

from services.adventure_service import (
    AdventureService,
    FLAGS,
    get_adventures_root,
)


class TestAdventureService(unittest.TestCase):
    """Tests for AdventureService listing, sorting, cover loading, and asset extraction."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.local_dir = Path(self.temp_dir.name)

        # Create sample adventures locally
        self.adv1_dir = self.local_dir / "Alpha Adventure"
        self.adv1_dir.mkdir(parents=True)
        (self.adv1_dir / "metadata.json").write_text(
            json.dumps({
                "id": "alpha-adventure",
                "title": "Alpha Adventure",
                "description": "First alpha test.",
                "created_at": "2026-08-10T10:00:00Z",
                "cover_image": "references/cover.png",
                "tags": ["Sci-Fi", "Action"],
            }),
            encoding="utf-8",
        )
        (self.adv1_dir / "theater.yaml").write_text("live_agent:\n  style: epic\n", encoding="utf-8")
        refs_dir = self.adv1_dir / "references"
        refs_dir.mkdir()
        (refs_dir / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\nfakeimage")
        pl_dir = self.adv1_dir / "playlists" / "battle"
        pl_dir.mkdir(parents=True)
        (pl_dir / "theme.mp3").write_bytes(b"ID3fakeaudio")
        lore_dir = self.adv1_dir / "lore"
        lore_dir.mkdir()
        (lore_dir / "history.txt").write_text("Lore history here", encoding="utf-8")

        self.adv2_dir = self.local_dir / "Beta Quest"
        self.adv2_dir.mkdir(parents=True)
        (self.adv2_dir / "metadata.json").write_text(
            json.dumps({
                "id": "beta-quest",
                "title": "Beta Quest",
                "description": "Second beta test.",
                "created_at": "2026-08-18T12:00:00Z",  # Newer than alpha
                "cover_image": "references/beta_cover.jpg",
                "tags": ["Fantasy"],
            }),
            encoding="utf-8",
        )
        (self.adv2_dir / "theater.yaml").write_text("live_agent:\n  style: mysterious\n", encoding="utf-8")
        refs2_dir = self.adv2_dir / "references"
        refs2_dir.mkdir()
        (refs2_dir / "beta_cover.jpg").write_bytes(b"\xff\xd8\xfffakejpeg")

        self.adv3_dir = self.local_dir / "Gamma Quest"
        self.adv3_dir.mkdir(parents=True)
        (self.adv3_dir / "metadata.json").write_text(
            json.dumps({
                "id": "gamma-quest",
                "title": "Gamma Quest",
                "description": "Third test with required stickies.",
                "created_at": "2026-08-20T14:00:00Z",
                "cover_image": "references/gamma_cover.png",
                "tags": ["Adventure"],
            }),
            encoding="utf-8",
        )
        (self.adv3_dir / "theater.yaml").write_text(
            "live_agent:\n  style: space-funk\nstory_planning:\n  adventure_mode: true\n  enforce_structured: true\n",
            encoding="utf-8",
        )
        (self.adv3_dir / "planning.yaml").write_text(
            "HUD:\n  owner: recorder\n  initial: 'HP: 10'\n"
            "Radar:\n  owner: recorder\n  hidden: true\n  initial: Clear\n",
            encoding="utf-8",
        )
        refs3_dir = self.adv3_dir / "references"
        refs3_dir.mkdir()
        (refs3_dir / "gamma_cover.png").write_bytes(b"\x89PNG\r\n\x1a\nfakeimage")
        lore3_dir = self.adv3_dir / "lore"
        lore3_dir.mkdir()
        (lore3_dir / "rules.txt").write_text("Quest rules", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_list_adventures_sorted_newest_first(self):
        service = AdventureService(base_dir=self.local_dir)
        adventures = service.list_adventures(force_refresh=True)

        self.assertEqual(len(adventures), 3)
        # Newest first: Gamma Quest (2026-08-20), Beta Quest (2026-08-18), Alpha Adventure (2026-08-10)
        self.assertEqual(adventures[0]["id"], "gamma-quest")
        self.assertEqual(adventures[1]["id"], "beta-quest")
        self.assertEqual(adventures[2]["id"], "alpha-adventure")
        self.assertEqual(adventures[0]["track_count"], 0)
        self.assertEqual(adventures[1]["track_count"], 0)
        self.assertEqual(adventures[2]["track_count"], 1)
        self.assertEqual(adventures[0]["lore_count"], 1)
        self.assertEqual(adventures[2]["lore_count"], 1)
        self.assertEqual(adventures[0]["reference_count"], 1)

    def test_get_adventure_and_cover(self):
        service = AdventureService(base_dir=self.local_dir)
        adv = service.get_adventure("alpha-adventure")
        self.assertIsNotNone(adv)
        self.assertEqual(adv["title"], "Alpha Adventure")

        cover_data, ctype = service.get_adventure_cover("alpha-adventure")
        self.assertIsNotNone(cover_data)
        self.assertIn("png", ctype.lower())

    def test_load_adventure_assets(self) -> None:
        lore_dir = self.adv1_dir / "lore"
        (lore_dir / "RESPONDER.md").write_text("Resolve scenes.", encoding="utf-8")
        (lore_dir / "PLANNER.md").write_text("Plan consequences.", encoding="utf-8")
        service = AdventureService(base_dir=self.local_dir)
        refs, playlists, lore, config = service.load_adventure_assets("alpha-adventure")

        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0][0].replace("\\", "/"), "references/cover.png")
        self.assertIn("battle", playlists)
        self.assertEqual(len(playlists["battle"]), 1)
        self.assertEqual(playlists["battle"][0][0], "theme.mp3")
        self.assertEqual({path.replace("\\", "/") for path, _ in lore}, {
            "lore/history.txt", "lore/RESPONDER.md", "lore/PLANNER.md",
        })
        self.assertEqual(service.get_adventure("alpha-adventure")["lore_count"], 3)
        self.assertIn("live_agent", config)
        self.assertEqual(config["live_agent"]["style"], "epic")

    def test_adventure_package_structure_and_planning_contract(self) -> None:
        service = AdventureService(base_dir=self.local_dir)
        adv = service.get_adventure("gamma-quest")
        self.assertIsNotNone(adv)
        self.assertEqual(adv["id"], "gamma-quest")
        self.assertEqual(adv["title"], "Gamma Quest")
        self.assertEqual(adv["lore_count"], 1)

        refs, playlists, lore, config = service.load_adventure_assets("gamma-quest")
        self.assertIn("story_planning", config)
        self.assertTrue(config["story_planning"]["adventure_mode"])
        self.assertNotIn("required_stickies", config["story_planning"])
        contracts = {name: content for name, content in refs}
        schema = yaml.safe_load(contracts["planning.yaml"])
        self.assertEqual(list(schema), ["HUD", "Radar"])
        self.assertTrue(schema["Radar"]["hidden"])

    def test_cache_invalidation(self):
        service = AdventureService(base_dir=self.local_dir)
        advs1 = service.list_adventures()
        self.assertEqual(len(advs1), 3)

        # Add a new adventure folder
        adv4_dir = self.local_dir / "Delta Quest"
        adv4_dir.mkdir(parents=True)
        (adv4_dir / "metadata.json").write_text(
            json.dumps({
                "id": "delta-quest",
                "title": "Delta Quest",
                "created_at": "2026-08-25T00:00:00Z",
            }),
            encoding="utf-8",
        )

        # Without refresh/invalidation, cache is returned
        self.assertEqual(len(service.list_adventures()), 3)

        # Invalidate cache
        service.invalidate_cache()
        advs2 = service.list_adventures()
        self.assertEqual(len(advs2), 4)
        self.assertEqual(advs2[0]["id"], "delta-quest")

    def test_get_adventures_root_flags(self):
        orig_local = FLAGS["testing_use_local"].value
        try:
            # Default (cloud storage path)
            FLAGS["testing_use_local"].value = False
            self.assertEqual(get_adventures_root(), Path("/mnt/storage/adventures"))

            # Local adventures flag
            FLAGS["testing_use_local"].value = True
            self.assertEqual(get_adventures_root().name, "adventures")
        finally:
            FLAGS["testing_use_local"].value = orig_local

    def test_warm_listing_does_not_access_storage(self) -> None:
        service = AdventureService(self.local_dir)
        expected = service.list_adventures()
        with patch.object(service, "_get_cache_key", side_effect=AssertionError("Unexpected storage scan")), \
             patch.object(service, "_fetch_adventures", side_effect=AssertionError("Unexpected reload")):
            self.assertEqual(service.list_adventures(), expected)

    def test_unchanged_catalog_does_not_reload(self) -> None:
        service = AdventureService(self.local_dir)
        service.list_adventures()
        with patch.object(service, "_fetch_adventures", wraps=service._fetch_adventures) as fetch:
            self.assertFalse(service.refresh_if_changed())
        fetch.assert_not_called()

    def test_metadata_edit_refreshes_cards(self) -> None:
        service = AdventureService(self.local_dir)
        service.list_adventures()
        path = self.adv1_dir / "metadata.json"
        metadata = json.loads(path.read_text(encoding="utf-8"))
        metadata["title"] = "Updated Adventure Title"
        path.write_text(json.dumps(metadata), encoding="utf-8")
        self.assertTrue(service.refresh_if_changed())
        self.assertEqual(service.get_adventure("alpha-adventure")["title"], "Updated Adventure Title")

    def test_asset_addition_and_removal_refresh_counts(self) -> None:
        service = AdventureService(self.local_dir)
        service.list_adventures()
        path = self.adv1_dir / "lore" / "new.md"
        path.write_text("New lore", encoding="utf-8")
        self.assertTrue(service.refresh_if_changed())
        self.assertEqual(service.get_adventure("alpha-adventure")["lore_count"], 2)
        path.unlink()
        self.assertTrue(service.refresh_if_changed())
        self.assertEqual(service.get_adventure("alpha-adventure")["lore_count"], 1)

    def test_added_and_removed_adventures_refresh_catalog(self) -> None:
        service = AdventureService(self.local_dir)
        service.list_adventures()
        folder = self.local_dir / "New Adventure"
        folder.mkdir()
        self.assertTrue(service.refresh_if_changed())
        self.assertEqual(len(service.list_adventures()), 4)
        folder.rmdir()
        self.assertTrue(service.refresh_if_changed())
        self.assertEqual(len(service.list_adventures()), 3)

    def test_failed_refresh_retains_snapshot_and_retries(self) -> None:
        service = AdventureService(self.local_dir)
        expected = service.list_adventures()
        (self.adv1_dir / "lore" / "new.txt").write_text("New", encoding="utf-8")
        with patch.object(service, "_fetch_adventures", side_effect=OSError("Storage unavailable")):
            with self.assertRaises(OSError):
                service.refresh_if_changed()
        self.assertEqual(service.list_adventures(), expected)
        self.assertTrue(service.refresh_if_changed())
        self.assertEqual(service.get_adventure("alpha-adventure")["lore_count"], 2)

    def test_refresh_serves_previous_snapshot_until_complete(self) -> None:
        service = AdventureService(self.local_dir)
        expected = service.list_adventures()
        original_fetch = service._fetch_adventures

        # Observe the request path from inside an in-progress background reload.
        def fetch_during_refresh() -> list[dict]:
            self.assertIs(service.list_adventures(), expected)
            return original_fetch()

        with patch.object(service, "_fetch_adventures", side_effect=fetch_during_refresh):
            self.assertTrue(service.refresh_if_changed(force_refresh=True))

    def test_service_respects_constructor_overrides(self):
        custom_dir = self.local_dir / "custom_adv"
        custom_dir.mkdir()

        # Constructor override
        service_custom = AdventureService(base_dir=custom_dir)
        self.assertEqual(service_custom.base_dir, custom_dir)
        self.assertEqual(service_custom.list_adventures(force_refresh=True), [])


if __name__ == "__main__":
    unittest.main()
