import os
import json
import time
from datetime import datetime
from typing import Optional


class ChatManager:
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.messages = []

        # Suggestion system: keyed by author username, one suggestion per user.
        # Each value: {"author": str, "text": str, "upvotes": set(), "created_at": float}
        self.suggestions: dict[str, dict] = {}

    def add_message(self, message: dict):
        self.messages.append(message)
        if len(self.messages) > 100:
            self.messages.pop(0)

    def get_messages(self):
        return self.messages

    # ------------------------------------------------------------------
    # Suggestion engine
    # ------------------------------------------------------------------

    def add_suggestion(self, author: str, text: str, profile_username: Optional[str] = None, profile_color: Optional[str] = None) -> dict:
        """Add or replace the author's active suggestion.

        Returns the stored suggestion dict (with ``upvote_count`` instead of
        the internal set).
        """
        if not author or not author.strip():
            raise ValueError("Suggestion author must be non-empty.")
        if not text or not text.strip():
            raise ValueError("Suggestion text must be non-empty.")

        suggestion = {
            "author": author.strip(),
            "text": text.strip(),
            "upvotes": set(),
            "created_at": time.time(),
        }
        author = author.strip()
        self._remove_suggestion_message(author)
        self.suggestions[author] = suggestion

        # Also add as a special message in the chat log so all viewers see it.
        message = {
            "author": author,
            "text": text.strip(),
            "type": "suggestion",
        }
        if profile_username:
            message["profile_username"] = profile_username
        if profile_color:
            message["profile_color"] = profile_color
        self.add_message(message)
        return self._serialize_suggestion(suggestion)

    def withdraw_suggestion(self, author: str) -> bool:
        """Remove the author's active suggestion. Returns True if one existed."""
        author = author.strip()
        removed = self.suggestions.pop(author, None) is not None
        if removed:
            self._remove_suggestion_message(author)
        return removed

    def upvote_suggestion(self, voter: str, target_author: str) -> bool:
        """Add *voter* to *target_author*'s suggestion upvotes.

        Returns False if the target suggestion does not exist or the voter is
        trying to upvote their own suggestion.
        """
        if not voter or not target_author:
            return False
        voter = voter.strip()
        target_author = target_author.strip()
        if voter == target_author:
            return False
        suggestion = self.suggestions.get(target_author)
        if suggestion is None:
            return False
        suggestion["upvotes"].add(voter)
        return True

    def get_suggestions(self) -> list[dict]:
        """Return all active suggestions sorted by descending upvote count
        then ascending creation time.
        """
        items = sorted(
            self.suggestions.values(),
            key=lambda s: (-len(s["upvotes"]), s["created_at"]),
        )
        return [self._serialize_suggestion(s) for s in items]

    def consume_top_suggestion(self) -> Optional[dict]:
        """Pop and return the top-ranked suggestion, or None if empty."""
        if not self.suggestions:
            return None
        top = min(
            self.suggestions.values(),
            key=lambda s: (-len(s["upvotes"]), s["created_at"]),
        )
        self.suggestions.pop(top["author"], None)
        self._remove_suggestion_message(top["author"])
        return self._serialize_suggestion(top)

    def export_suggestions(self) -> list[dict]:
        """Return JSON-safe active suggestions for theater persistence."""
        return self.get_suggestions()

    def load_suggestions(self, suggestions: list[dict]) -> None:
        """Restore persisted suggestions, tolerating malformed older state."""
        self.suggestions = {}
        for item in suggestions if isinstance(suggestions, list) else []:
            author = item.get("author") if isinstance(item, dict) else None
            text = item.get("text") if isinstance(item, dict) else None
            if not isinstance(author, str) or not author.strip() or not isinstance(text, str) or not text.strip():
                continue
            try:
                created_at = float(item.get("created_at", time.time()))
                upvoters = {
                    voter for voter in item.get("upvoters", [])
                    if isinstance(voter, str) and voter.strip()
                }
            except (TypeError, ValueError):
                created_at, upvoters = time.time(), set()
            self.suggestions[author.strip()] = {
                "author": author.strip(), "text": text.strip(),
                "upvotes": upvoters, "created_at": created_at,
            }

    def _remove_suggestion_message(self, author: str) -> None:
        self.messages = [
            message for message in self.messages
            if not (message.get("type") == "suggestion" and message.get("author") == author)
        ]

    @staticmethod
    def _serialize_suggestion(suggestion: dict) -> dict:
        """Convert internal suggestion (with set) to a JSON-safe dict."""
        return {
            "author": suggestion["author"],
            "text": suggestion["text"],
            "upvote_count": len(suggestion["upvotes"]),
            "upvoters": sorted(suggestion["upvotes"]),
            "created_at": suggestion["created_at"],
        }

    # ------------------------------------------------------------------
    # Chat export
    # ------------------------------------------------------------------

    def export_and_reset(self, image_id: str):
        if not self.messages:
            return  # Nothing to export

        os.makedirs(self.output_dir, exist_ok=True)

        # Only alphanumeric + dots/dashes
        safe_id = "".join([c for c in str(image_id) if c.isalnum() or c in ('_', '-', '.')]) if image_id else "unknown_image"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        filename = f"chat_{safe_id}_{timestamp}.json"
        filepath = os.path.join(self.output_dir, filename)

        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(self.messages, f, indent=2)
            print(f"[CHAT MANAGER] Exported {len(self.messages)} messages to {filepath}")
        except Exception as e:
            print(f"[CHAT MANAGER] Failed to export chat log: {e}")

        # Reset chat after export
        self.messages = []
