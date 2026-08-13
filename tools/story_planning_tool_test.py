import json
import time
import unittest
from unittest.mock import MagicMock, patch

from tools.story_planning_tool import (
    DEFAULT_MAX_NAMED_ELEMENTS,
    StoryPlanningTools,
    compute_elements_fingerprint,
    build_script_prompt,
)
from providers.text_response_provider import (
    TextResponseProvider,
    TextResponseRequest,
    TextResponseResult,
    TextResponseProviderError,
)


class TestBuildScriptPrompt(unittest.TestCase):
    def test_build_script_prompt_empty_elements(self):
        prompt = build_script_prompt(
            elements=[],
            reused_nodes=[],
            needed_count=3,
            start_idx=0,
        )
        self.assertIn("(No active named elements)", prompt)
        self.assertIn("Generate exactly 3 new upcoming script node(s) starting at node_index 0.", prompt)

    def test_build_script_prompt_with_elements_and_reused_nodes(self):
        elements = [
            {"name": "hero", "content": "Mara, a bold cartographer"},
            {"name": "setting", "content": "Sunken Temple"},
        ]
        reused_nodes = [
            {
                "node_index": 0,
                "narration": "Entered the sunken temple.",
                "expected_user_response": "Look around or press forward?",
            }
        ]
        prompt = build_script_prompt(
            elements=elements,
            reused_nodes=reused_nodes,
            needed_count=2,
            start_idx=1,
        )
        self.assertIn("- hero: Mara, a bold cartographer", prompt)
        self.assertIn("- setting: Sunken Temple", prompt)
        self.assertIn("Prior narrative nodes already established:", prompt)
        self.assertIn("Node 0: Narration: Entered the sunken temple.", prompt)
        self.assertIn("Generate exactly 2 new upcoming script node(s) starting at node_index 1.", prompt)


class MockTextResponseProvider(TextResponseProvider):
    id = "mock-text-provider"
    display_name = "Mock Text Provider"
    model = "mock-model"

    def __init__(self, response_text: str | None = None):
        self.response_text = response_text
        self.last_request: TextResponseRequest | None = None
        self.should_fail = False

    def generate(self, request: TextResponseRequest) -> TextResponseResult:
        self.last_request = request
        if self.should_fail:
            raise TextResponseProviderError("Provider unavailable")
        default_json = json.dumps([
            {
                "node_index": 0,
                "narration": "The hero enters the dark cavern.",
                "expected_user_response": "Will you ignite a torch or tread carefully in the shadows?",
            },
            {
                "node_index": 1,
                "narration": "Strange glowing runes illuminate the cavern wall.",
                "expected_user_response": "Do you touch the runes or read them aloud?",
            },
            {
                "node_index": 2,
                "narration": "A heavy stone door begins to grind open.",
                "expected_user_response": "Do you rush inside or wait for guards to emerge?",
            },
        ])
        return TextResponseResult(
            text=self.response_text if self.response_text is not None else default_json,
            provider=self.id,
            model=self.model,
        )


class TestStoryPlanningTools(unittest.TestCase):
    def setUp(self):
        self.tools = StoryPlanningTools(theater_id="test_theater")

    def test_update_or_insert_and_snapshot_preserve_named_elements(self):
        self.assertEqual(
            self.tools.update_or_insert_named_element("hero", "Mara, a bold cartographer."),
            "Added named element 'hero'.",
        )
        self.assertEqual(
            self.tools.update_or_insert_named_element("setting", "A flooded observatory."),
            "Added named element 'setting'.",
        )
        self.assertEqual(
            self.tools.update_or_insert_named_element("hero", "Mara, now carrying the star map."),
            "Updated named element 'hero'.",
        )

        present = self.tools.get_present_elements()
        self.assertEqual(
            present,
            [
                {"name": "setting", "content": "A flooded observatory."},
                {"name": "hero", "content": "Mara, now carrying the star map."},
            ],
        )

    def test_element_overflow_drops_oldest(self):
        for index in range(DEFAULT_MAX_NAMED_ELEMENTS):
            self.tools.update_or_insert_named_element(f"key_{index}", f"value_{index}")

        present = self.tools.get_present_elements()
        self.assertEqual(len(present), DEFAULT_MAX_NAMED_ELEMENTS)
        self.assertEqual(present[0]["name"], "key_0")

        self.tools.update_or_insert_named_element("key_overflow", "value_overflow")
        present = self.tools.get_present_elements()
        self.assertEqual(len(present), DEFAULT_MAX_NAMED_ELEMENTS)
        self.assertEqual(present[0]["name"], "key_1")
        self.assertEqual(present[-1]["name"], "key_overflow")

    def test_clear_scene(self):
        self.tools.update_or_insert_named_element("key", "value")
        self.assertEqual(
            self.tools.clear_scene(),
            "Cleared 1 named element(s) from the scene.",
        )
        self.assertEqual(self.tools.get_present_elements(), [])

    def test_get_script_piece_instance_method(self):
        provider = MockTextResponseProvider()
        self.tools.text_provider = provider
        self.tools.update_or_insert_named_element("hero", "Mara")

        node = self.tools.get_script_piece()

        self.assertIsInstance(node, dict)
        self.assertEqual(node["node_index"], 0)
        self.assertIn("dark cavern", node["narration"])
        self.assertIn("ignite a torch", node["expected_user_response"])
        self.assertIsNotNone(provider.last_request)
        self.assertIn("hero: Mara", provider.last_request.prompt)

        # Node 0 popped; 2 remain (refill fires at 1, not yet)
        cached = self.tools.get_cached_script()
        self.assertEqual(len(cached), 2)
        self.assertEqual(cached[0]["node_index"], 1)

    def test_get_script_piece_reuses_prior_script(self):
        provider = MockTextResponseProvider()
        self.tools.text_provider = provider
        self.tools.update_or_insert_named_element("hero", "Mara")
        fp = compute_elements_fingerprint([{"name": "hero", "content": "Mara"}])

        prior_script = [
            {
                "node_index": 0,
                "narration": "Prior node 0",
                "expected_user_response": "Choice 0",
                "elements_fingerprint": fp,
            },
            {
                "node_index": 1,
                "narration": "Prior node 1",
                "expected_user_response": "Choice 1",
                "elements_fingerprint": fp,
            },
        ]
        with self.tools._script_lock:
            self.tools._cached_script = list(prior_script)

        # Pop node 0 — should come from cache without a new LLM call
        node0 = self.tools.get_script_piece()
        self.assertEqual(node0["narration"], "Prior node 0")

        # Pop node 1 — still from cache
        node1 = self.tools.get_script_piece()
        self.assertEqual(node1["narration"], "Prior node 1")

    def test_get_script_piece_validation(self):
        self.tools.nodes_ahead = -1
        with self.assertRaises(ValueError):
            self.tools.get_script_piece()

    def test_get_script_piece_escalates_provider_error(self):
        provider = MockTextResponseProvider()
        provider.should_fail = True
        self.tools.text_provider = provider

        with self.assertRaises(TextResponseProviderError):
            self.tools.get_script_piece()

    def test_update_script_async(self):
        provider = MockTextResponseProvider()
        self.tools.text_provider = provider
        self.tools.update_or_insert_named_element("hero", "Mara")

        result_container = []

        def callback(nodes):
            result_container.extend(nodes)

        self.tools.update_script_async(callback=callback)

        time.sleep(0.3)
        self.assertEqual(len(result_container), 3)
        self.assertEqual(len(self.tools.get_cached_script()), 3)

    def test_config_story_planning_options(self):
        provider = MockTextResponseProvider()
        config = {
            "nodes_ahead": 5,
            "adventure_mode": True,
            "max_named_elements": 2,
            "initial_elements": {"hero": "Kael, an elemental mage"},
            "text_provider": provider,
        }
        tools = StoryPlanningTools(config=config, theater_id="adv_stage")

        self.assertEqual(tools.nodes_ahead, 5)
        self.assertTrue(tools.adventure_mode)
        self.assertEqual(tools.max_named_elements, 2)
        self.assertEqual(
            tools.get_present_elements(),
            [{"name": "hero", "content": "Kael, an elemental mage"}],
        )

        tools.update_or_insert_named_element("item", "Magic Wand")
        tools.update_or_insert_named_element("location", "Enchanted Forest")
        # Capacity is 2, so "hero" should have been evicted when adding 3rd element
        elements = tools.get_present_elements()
        self.assertEqual(len(elements), 2)
        names = [e["name"] for e in elements]
        self.assertEqual(names, ["item", "location"])

        node = tools.get_script_piece()
        self.assertIsInstance(node, dict)
        self.assertIn("node_index", node)
        self.assertIsNotNone(provider.last_request)
        self.assertIn("Craft dramatic interactive decision points", provider.last_request.system_instruction)

    def test_update_named_element_cooldown(self):
        config = {"cooldown_duration": 10.0}
        tools = StoryPlanningTools(config=config, theater_id="cd_stage")

        res1 = tools.update_or_insert_named_element("hero", "Mara")
        self.assertIn("Added named element", res1)

        res2 = tools.update_or_insert_named_element("hero", "Mara updated")
        self.assertIn("is on cooldown", res2)

    def test_get_script_piece_cooldown(self):
        provider = MockTextResponseProvider()
        config = {"cooldown_duration": 10.0, "text_provider": provider}
        tools = StoryPlanningTools(config=config, theater_id="cd_stage2")

        tools.get_script_piece()

        res2 = tools.get_script_piece()
        self.assertIn("is on cooldown", res2)

    @patch("tools.story_planning_tool.get_text_response_provider")
    def test_config_provider_id_and_options(self, mock_get_provider):
        mock_provider = MagicMock()
        mock_provider.id = "gemini-3"
        mock_get_provider.return_value = mock_provider
        config = {
            "provider": "gemini-3",
            "provider_options": {"model": "gemini-3.6-flash"},
        }
        tools = StoryPlanningTools(config=config, theater_id="provider_cfg_stage")
        self.assertEqual(tools.provider_id, "gemini-3")
        self.assertEqual(tools.provider_options, {"model": "gemini-3.6-flash"})
        provider = tools._get_text_provider()
        self.assertIsNotNone(provider)
        self.assertEqual(getattr(provider, "id", None), "gemini-3")
        mock_get_provider.assert_called_once_with("gemini-3", {"model": "gemini-3.6-flash"})

    def test_get_tools_exposes_get_script_piece_only_in_adventure_mode(self):
        default_tools = StoryPlanningTools(config={"adventure_mode": False}, theater_id="t1")
        tools_list = default_tools.get_tools()
        tool_names = [t.__name__ for t in tools_list]
        self.assertIn("update_or_insert_named_element", tool_names)
        self.assertIn("clear_scene", tool_names)
        self.assertNotIn("get_script_piece", tool_names)

        adv_tools = StoryPlanningTools(config={"adventure_mode": True}, theater_id="t2")
        adv_tools_list = adv_tools.get_tools()
        adv_tool_names = [t.__name__ for t in adv_tools_list]
        self.assertIn("update_or_insert_named_element", adv_tool_names)
        self.assertIn("clear_scene", adv_tool_names)
        self.assertIn("get_script_piece", adv_tool_names)

    def test_element_update_triggers_script_rebuild_in_adventure_mode(self):
        provider = MockTextResponseProvider()
        config = {
            "adventure_mode": True,
            "text_provider": provider,
        }
        tools = StoryPlanningTools(config=config, theater_id="adv_rebuild")
        tools.update_or_insert_named_element("hero", "Mara")

        time.sleep(0.3)
        self.assertEqual(len(tools.get_cached_script()), 3)

    def test_noop_element_update_does_not_invalidate_script_cache(self):
        """Re-setting an element to the exact same content must not clear the script cache."""
        provider = MockTextResponseProvider()
        config = {"adventure_mode": True, "text_provider": provider}
        tools = StoryPlanningTools(config=config, theater_id="noop_stage")

        # Seed the cache manually with a known fingerprint
        fp = compute_elements_fingerprint([{"name": "hero", "content": "Mara"}])
        sentinel = {
            "node_index": 0,
            "narration": "Sentinel node",
            "expected_user_response": "Stay or go?",
            "elements_fingerprint": fp,
        }
        with tools._elements_lock:
            tools._elements["hero"] = "Mara"
        with tools._script_lock:
            tools._cached_script = [sentinel]

        # Update with identical content — fingerprint must not change
        tools.update_or_insert_named_element("hero", "Mara")

        # Cache should be untouched
        cached = tools.get_cached_script()
        self.assertEqual(len(cached), 1)
        self.assertEqual(cached[0]["narration"], "Sentinel node")

    def test_element_content_change_clears_cache_immediately(self):
        """Changing element content must wipe stale cached nodes synchronously before async refill."""
        provider = MockTextResponseProvider()
        config = {"adventure_mode": True, "text_provider": provider}
        tools = StoryPlanningTools(config=config, theater_id="invalidation_stage")

        # Seed element + a stale cached node carrying its fingerprint
        old_fp = compute_elements_fingerprint([{"name": "hero", "content": "Mara"}])
        stale_node = {
            "node_index": 0,
            "narration": "Old scene node",
            "expected_user_response": "What do you do?",
            "elements_fingerprint": old_fp,
        }
        with tools._elements_lock:
            tools._elements["hero"] = "Mara"
        with tools._script_lock:
            tools._cached_script = [stale_node]

        # Change the content — new fingerprint → cache must be cleared immediately
        tools.update_or_insert_named_element("hero", "Mara the Bold")

        # Cache cleared synchronously; stale node must be gone even if async refill raced in
        cached = tools.get_cached_script()
        narrations = [n["narration"] for n in cached]
        self.assertNotIn("Old scene node", narrations)

    def test_story_script_logging(self):
        provider = MockTextResponseProvider()
        tools = StoryPlanningTools(
            config={"text_provider": provider},
            theater_id="logging_stage",
        )
        with self.assertLogs("tools.story_planning_tool", level="INFO") as cm:
            tools.update_or_insert_named_element("hero", "Mara")
            tools.generate_character(name="Sylvia", personality="Brave", motivation="Save kingdom", quirk="Flips coin")
            tools.get_script_piece()
            tools.clear_scene()

        log_output = "\n".join(cm.output)
        self.assertIn("[STORY_SCRIPT]", log_output)
        self.assertIn("Active Characters:", log_output)
        self.assertIn("- Sylvia:", log_output)
        self.assertIn("Script nodes active", log_output)
        self.assertIn("[Node 0]", log_output)
        self.assertIn("Cleared", log_output)

    def test_log_filter_with_story_script_prefix(self):
        from main import LogFilter
        import logging

        script_filter = LogFilter(prefix="[STORY_SCRIPT]")

        rec_script = logging.LogRecord(
            name="tools.story_planning_tool",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="[STORY_SCRIPT] Active nodes...",
            args=(),
            exc_info=None,
        )
        rec_other = logging.LogRecord(
            name="api_server.app",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="GET /api/status HTTP/1.1",
            args=(),
            exc_info=None,
        )

        self.assertTrue(script_filter.filter(rec_script))
        self.assertFalse(script_filter.filter(rec_other))

    def test_generate_character_explicit(self):
        tools = StoryPlanningTools(config={"adventure_mode": True}, theater_id="char_test")
        res = tools.generate_character(
            name="Vaelen",
            description="Elven rogue",
            personality="Cynical yet loyal",
            motivation="Recover the stolen sun orb",
            quirk="Never sits with back to doors",
        )
        self.assertIn("Created/Updated character 'Vaelen'", res)
        chars = tools.get_present_characters()
        self.assertEqual(len(chars), 1)
        self.assertEqual(chars[0]["name"], "Vaelen")
        self.assertEqual(chars[0]["personality"], "Cynical yet loyal")
        self.assertEqual(chars[0]["motivation"], "Recover the stolen sun orb")
        self.assertEqual(chars[0]["quirk"], "Never sits with back to doors")

    def test_generate_character_llm_generated_and_random_quirk(self):
        llm_json = json.dumps({"personality": "Daring and witty", "motivation": "Find lost treasure"})
        provider = MockTextResponseProvider(response_text=llm_json)
        tools = StoryPlanningTools(config={"adventure_mode": True, "text_provider": provider}, theater_id="char_llm")
        res = tools.generate_character(name="Kael")
        self.assertIn("Created/Updated character 'Kael'", res)
        chars = tools.get_present_characters()
        self.assertEqual(len(chars), 1)
        self.assertEqual(chars[0]["personality"], "Daring and witty")
        self.assertEqual(chars[0]["motivation"], "Find lost treasure")
        self.assertTrue(len(chars[0]["quirk"]) > 0)

    def test_adventure_mode_gating_character_tool(self):
        non_adv = StoryPlanningTools(config={"adventure_mode": False})
        adv = StoryPlanningTools(config={"adventure_mode": True})

        non_adv_tools = non_adv.get_tools()
        adv_tools = adv.get_tools()

        self.assertNotIn(non_adv.generate_character, non_adv_tools)
        self.assertIn(adv.generate_character, adv_tools)
        self.assertIn(adv.get_script_piece, adv_tools)

    def test_export_and_import_story_planning_state(self):
        tools = StoryPlanningTools(config={"adventure_mode": True}, theater_id="export_import")
        tools.update_or_insert_named_element("location", "Crystal Cave")
        tools.generate_character(name="Sylvia", personality="Brave", motivation="Save the enclave")

        exported = tools.export_story_planning_state()
        self.assertEqual(len(exported["named_elements"]), 1)
        self.assertEqual(len(exported["characters"]), 1)

        new_tools = StoryPlanningTools(theater_id="import_target")
        new_tools.import_story_planning_state(exported)

        self.assertEqual(len(new_tools.get_present_elements()), 1)
        self.assertEqual(len(new_tools.get_present_characters()), 1)
        self.assertEqual(new_tools.get_present_characters()[0]["name"], "Sylvia")

    def test_fingerprint_changes_with_characters(self):
        fp1 = compute_elements_fingerprint([{"name": "elem1", "content": "val1"}], [])
        fp2 = compute_elements_fingerprint(
            [{"name": "elem1", "content": "val1"}],
            [{"name": "char1", "personality": "bold", "motivation": "glory"}],
        )
        self.assertNotEqual(fp1, fp2)


if __name__ == "__main__":
    unittest.main()

