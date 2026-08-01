import time
import unittest

from components.chat_manager import ChatManager


class TestChatManagerSuggestions(unittest.TestCase):
    """Tests for the ChatManager suggestion engine."""

    def setUp(self):
        self.cm = ChatManager(output_dir="/tmp/test_chat")

    # --- add_suggestion ---

    def test_add_suggestion_creates_entry(self):
        result = self.cm.add_suggestion("alice", "Go to the moon")
        self.assertEqual(result["author"], "alice")
        self.assertEqual(result["text"], "Go to the moon")
        self.assertEqual(result["upvote_count"], 0)
        self.assertIn("alice", self.cm.suggestions)

    def test_add_suggestion_adds_chat_message(self):
        self.cm.add_suggestion("alice", "Fly higher")
        msgs = self.cm.get_messages()
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["type"], "suggestion")
        self.assertEqual(msgs[0]["text"], "Fly higher")

    def test_add_suggestion_replaces_existing(self):
        self.cm.add_suggestion("alice", "First idea")
        self.cm.add_suggestion("alice", "Better idea")
        suggestions = self.cm.get_suggestions()
        # Only one suggestion per user
        alice_suggestions = [s for s in suggestions if s["author"] == "alice"]
        self.assertEqual(len(alice_suggestions), 1)
        self.assertEqual(alice_suggestions[0]["text"], "Better idea")
        self.assertEqual(len(self.cm.get_messages()), 1)

    def test_add_suggestion_strips_whitespace(self):
        result = self.cm.add_suggestion("  bob  ", "  trim me  ")
        self.assertEqual(result["author"], "bob")
        self.assertEqual(result["text"], "trim me")

    def test_add_suggestion_empty_author_raises(self):
        with self.assertRaises(ValueError):
            self.cm.add_suggestion("", "some text")

    def test_add_suggestion_empty_text_raises(self):
        with self.assertRaises(ValueError):
            self.cm.add_suggestion("alice", "")

    # --- withdraw_suggestion ---

    def test_withdraw_removes_suggestion(self):
        self.cm.add_suggestion("alice", "idea")
        self.assertTrue(self.cm.withdraw_suggestion("alice"))
        self.assertEqual(len(self.cm.get_suggestions()), 0)
        self.assertEqual(len(self.cm.get_messages()), 0)

    def test_withdraw_nonexistent_returns_false(self):
        self.assertFalse(self.cm.withdraw_suggestion("nobody"))

    # --- upvote_suggestion ---

    def test_upvote_adds_voter(self):
        self.cm.add_suggestion("alice", "idea")
        result = self.cm.upvote_suggestion("bob", "alice")
        self.assertTrue(result)
        suggestions = self.cm.get_suggestions()
        self.assertEqual(suggestions[0]["upvote_count"], 1)
        self.assertIn("bob", suggestions[0]["upvoters"])

    def test_upvote_self_returns_false(self):
        self.cm.add_suggestion("alice", "idea")
        result = self.cm.upvote_suggestion("alice", "alice")
        self.assertFalse(result)

    def test_upvote_nonexistent_returns_false(self):
        result = self.cm.upvote_suggestion("bob", "nobody")
        self.assertFalse(result)

    def test_upvote_idempotent(self):
        self.cm.add_suggestion("alice", "idea")
        self.cm.upvote_suggestion("bob", "alice")
        self.cm.upvote_suggestion("bob", "alice")
        suggestions = self.cm.get_suggestions()
        self.assertEqual(suggestions[0]["upvote_count"], 1)

    # --- get_suggestions ordering ---

    def test_get_suggestions_ordered_by_votes_then_time(self):
        self.cm.add_suggestion("alice", "first")
        time.sleep(0.01)
        self.cm.add_suggestion("bob", "second")
        # Bob gets an upvote, should rank first
        self.cm.upvote_suggestion("charlie", "bob")
        suggestions = self.cm.get_suggestions()
        self.assertEqual(len(suggestions), 2)
        self.assertEqual(suggestions[0]["author"], "bob")
        self.assertEqual(suggestions[1]["author"], "alice")

    def test_get_suggestions_tiebreak_by_time(self):
        self.cm.add_suggestion("alice", "first")
        time.sleep(0.01)
        self.cm.add_suggestion("bob", "second")
        # No votes — earliest should rank first
        suggestions = self.cm.get_suggestions()
        self.assertEqual(suggestions[0]["author"], "alice")
        self.assertEqual(suggestions[1]["author"], "bob")

    # --- consume_top_suggestion ---

    def test_consume_returns_top_and_removes(self):
        self.cm.add_suggestion("alice", "idea A")
        self.cm.add_suggestion("bob", "idea B")
        self.cm.upvote_suggestion("charlie", "bob")
        consumed = self.cm.consume_top_suggestion()
        self.assertEqual(consumed["author"], "bob")
        self.assertEqual(consumed["text"], "idea B")
        # Bob's suggestion removed, only alice remains
        remaining = self.cm.get_suggestions()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["author"], "alice")
        self.assertEqual(len(self.cm.get_messages()), 1)

    def test_consume_empty_returns_none(self):
        self.assertIsNone(self.cm.consume_top_suggestion())

    # --- one-per-user enforcement ---

    def test_one_suggestion_per_user(self):
        self.cm.add_suggestion("alice", "first")
        self.cm.add_suggestion("bob", "bob's idea")
        self.cm.add_suggestion("alice", "replaced")
        self.assertEqual(len(self.cm.get_suggestions()), 2)
        alice_s = [s for s in self.cm.get_suggestions() if s["author"] == "alice"]
        self.assertEqual(alice_s[0]["text"], "replaced")

    # --- upvotes reset when suggestion is replaced ---

    def test_replacing_suggestion_resets_upvotes(self):
        self.cm.add_suggestion("alice", "first")
        self.cm.upvote_suggestion("bob", "alice")
        self.assertEqual(self.cm.get_suggestions()[0]["upvote_count"], 1)
        # Replace suggestion
        self.cm.add_suggestion("alice", "new idea")
        self.assertEqual(self.cm.get_suggestions()[0]["upvote_count"], 0)

    def test_suggestions_round_trip_through_json_safe_export(self):
        self.cm.add_suggestion("alice", "idea")
        self.cm.upvote_suggestion("bob", "alice")
        exported = self.cm.export_suggestions()
        restored = ChatManager(output_dir="/tmp/test_chat")
        restored.load_suggestions(exported)
        self.assertEqual(restored.get_suggestions()[0]["upvote_count"], 1)


if __name__ == "__main__":
    unittest.main()
