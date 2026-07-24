import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

class NotesTools:
    def __init__(self, config: dict):
        root_dir = Path(__file__).parent.parent.resolve()
        notes_folder_arg = config.get("notes_folder")
        if notes_folder_arg:
            self.notes_dir = str(Path(notes_folder_arg).resolve())
            self._has_custom_folder = True
        else:
            self.notes_dir = str((root_dir / "sessions").resolve())
            self._has_custom_folder = False
        os.makedirs(self.notes_dir, exist_ok=True)

    def get_effective_notes_dir(self) -> str:
        """Return active deployed session notes directory if present, otherwise default self.notes_dir."""
        if getattr(self, "_has_custom_folder", False):
            return self.notes_dir
        sessions_dir = Path(__file__).parent.parent / "sessions"
        if sessions_dir.exists():
            for entry in sessions_dir.iterdir():
                if entry.is_dir():
                    meta_path = entry / "session.json"
                    if meta_path.exists():
                        try:
                            with open(meta_path, "r", encoding="utf-8") as f:
                                data = json.load(f)
                                if data.get("status") == "deployed":
                                    notes_dir = entry / "notes"
                                    notes_dir.mkdir(parents=True, exist_ok=True)
                                    return str(notes_dir.resolve())
                        except Exception:
                            pass
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
