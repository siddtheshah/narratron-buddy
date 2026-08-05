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
                {"name": "hero", "content": "Mara, now carrying the star map."},
                {"name": "setting", "content": "A flooded observatory."},
            ],
        )

    def test_scene_is_limited_to_five_distinct_elements(self):
        for index in range(MAX_NAMED_ELEMENTS):
            self.assertIn("Added", self.tools.upsert_named_element(f"element-{index}", "value"))
        self.assertIn("already has 5", self.tools.upsert_named_element("overflow", "value"))
        self.assertIn("Updated", self.tools.upsert_named_element("element-0", "replacement"))

    def test_clear_scene_removes_all_elements(self):
        self.tools.upsert_named_element("hero", "Mara")
        self.assertEqual(self.tools.clear_scene(), "Cleared 1 named element(s) from the scene.")
        self.assertEqual(self.tools.get_present_elements(), [])
