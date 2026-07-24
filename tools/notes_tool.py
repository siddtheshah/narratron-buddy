import json
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

class NotesTools:
    def __init__(self, config: dict, session_id: str):
        root_dir = Path(__file__).parent.parent.resolve()
        self.active_session_id: str = session_id

        self.notes_dir = str((root_dir / "sessions" / self.active_session_id / "output" / "artifacts" / "notes").resolve())
        os.makedirs(self.notes_dir, exist_ok=True)

    def get_effective_notes_dir(self) -> str:
        """Return active session notes directory."""
        return self.notes_dir

    def edit_notes(self, note_name: str, content: str) -> str:
        """Create or edit a note file with the given content under active session notes directory.

        Args:
            note_name: The name of the note (e.g. 'characters.txt' or 'characters').
            content: The text content to write to the note.

        Returns:
            A status message indicating success or failure.
        """
        try:
            filename = note_name
            if not filename.endswith(".txt"):
                filename += ".txt"
            
            # Guard against directory traversal
            filename = os.path.basename(filename)
            effective_dir = self.get_effective_notes_dir()
            filepath = os.path.join(effective_dir, filename)
            
            # Write content
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            
            logger.info(f"Saved note '{filename}' at {filepath}.")
            return f"Successfully saved note '{filename}'."
        except Exception as e:
            logger.error(f"Error editing note: {e}")
            return f"Error editing note: {e}"

    def delete_notes(self, note_name: str) -> str:
        """Delete a note file under active session notes directory.

        Args:
            note_name: The name of the note to delete (e.g. 'characters.txt' or 'characters').

        Returns:
            A status message indicating success or failure.
        """
        try:
            filename = note_name
            if not filename.endswith(".txt"):
                filename += ".txt"
            
            filename = os.path.basename(filename)
            effective_dir = self.get_effective_notes_dir()
            filepath = os.path.join(effective_dir, filename)
            
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.info(f"Deleted note '{filename}'.")
                return f"Successfully deleted note '{filename}'."
            else:
                return f"Error: Note '{filename}' not found."
        except Exception as e:
            logger.error(f"Error deleting note: {e}")
            return f"Error deleting note: {e}"
