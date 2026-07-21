import os

class NotesTools:
    def __init__(self, config: dict):
        # Save notes in "output/artifacts/notes"
        self.notes_dir = os.path.join("output", "artifacts", "notes")
        os.makedirs(self.notes_dir, exist_ok=True)

    def edit_notes(self, note_name: str, content: str) -> str:
        """Create or edit a note file with the given content under artifacts/notes.

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
            filepath = os.path.join(self.notes_dir, filename)
            
            # Write content
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(content)
            
            return f"Successfully saved note '{filename}' under artifacts/notes."
        except Exception as e:
            return f"Error editing note: {e}"

    def delete_notes(self, note_name: str) -> str:
        """Delete a note file under artifacts/notes.

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
            filepath = os.path.join(self.notes_dir, filename)
            
            if os.path.exists(filepath):
                os.remove(filepath)
                return f"Successfully deleted note '{filename}'."
            else:
                return f"Error: Note '{filename}' not found."
        except Exception as e:
            return f"Error deleting note: {e}"
