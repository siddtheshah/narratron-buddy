import os
import json
from datetime import datetime

class ChatManager:
    def __init__(self, output_dir: str):
        self.output_dir = output_dir
        self.messages = []

    def add_message(self, message: dict):
        self.messages.append(message)
        if len(self.messages) > 100:
            self.messages.pop(0)

    def get_messages(self):
        return self.messages

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
