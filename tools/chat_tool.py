class ChatTools:
    def __init__(self, config: dict = None):
        self.on_send_chat_message = None

    def send_chat_message(self, text: str) -> str:
        """Sends a text message/response to the user chat window.

        Args:
            text: The text message to send to the user.

        Returns:
            A status message indicating success or failure.
        """
        try:
            print(f"[chat_tool] Sending chat message: {text}")
            if self.on_send_chat_message:
                self.on_send_chat_message(text)
            return f"Successfully sent chat message to the user: {text}"
        except Exception as e:
            print(f"[chat_tool] Error sending chat message: {e}")
            return f"Error sending chat message: {e}"
