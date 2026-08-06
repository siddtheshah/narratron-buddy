import unittest

from tools.named_element_tool import MAX_NAMED_ELEMENTS, NamedElementTools


class TestNamedElementTools(unittest.TestCase):
    def setUp(self):
        self.tools = NamedElementTools(theater_id="test_theater")

    def test_upsert_and_snapshot_preserve_named_elements(self):
        self.assertEqual(self.tools.upsert_named_element("hero", "Mara, a bold cartographer."), "Added named element 'hero'.")
        self.assertEqual(self.tools.upsert_named_element("setting", "A flooded observatory."), "Added named element 'setting'.")
        self.assertEqual(self.tools.upsert_named_element("hero", "Mara, now carrying the star map."), "Updated named element 'hero'.")
        self.assertEqual(
            self.tools.get_present_elements(),
            [
                {"name": "setting", "content": "A flooded observatory."},
                {"name": "hero", "content": "Mara, now carrying the star map."},
            ],
        )

    def test_lru_eviction_when_exceeding_max_elements(self):
        for index in range(MAX_NAMED_ELEMENTS):
            self.assertEqual(
                self.tools.upsert_named_element(f"element-{index}", f"value-{index}"),
                f"Added named element 'element-{index}'.",
            )
        self.assertEqual(
            self.tools.upsert_named_element("overflow", "overflow-value"),
            "Added named element 'overflow'.",
        )
        present = self.tools.get_present_elements()
        self.assertEqual(len(present), MAX_NAMED_ELEMENTS)
        self.assertNotIn({"name": "element-0", "content": "value-0"}, present)
        self.assertEqual(present[-1], {"name": "overflow", "content": "overflow-value"})

    def test_lru_update_refreshes_recency(self):
        for index in range(MAX_NAMED_ELEMENTS):
            self.tools.upsert_named_element(f"element-{index}", f"value-{index}")

        # Update element-0, moving it to the end (most recent).
        self.assertEqual(
            self.tools.upsert_named_element("element-0", "updated-value-0"),
            "Updated named element 'element-0'.",
        )
        # Adding a 6th element should evict element-1 (the new least recently used), not element-0.
        self.tools.upsert_named_element("overflow", "overflow-value")
        present_names = [e["name"] for e in self.tools.get_present_elements()]
        self.assertNotIn("element-1", present_names)
        self.assertIn("element-0", present_names)
        self.assertIn("overflow", present_names)

    def test_clear_scene_removes_all_elements(self):
        self.tools.upsert_named_element("hero", "Mara")
        self.assertEqual(self.tools.clear_scene(), "Cleared 1 named element(s) from the scene.")
        self.assertEqual(self.tools.get_present_elements(), [])
