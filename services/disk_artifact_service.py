import os
import mimetypes
from pathlib import Path
from typing import Any, Optional, Union
from google.genai import types
from google.adk.artifacts.base_artifact_service import BaseArtifactService, ArtifactVersion, ensure_part
from google.adk.errors.input_validation_error import InputValidationError
from components.theater_manager import ensure_ephemeral_root

class DiskArtifactService(BaseArtifactService):
    """A disk-file based implementation of ADK's artifact service.
    
    Files are saved directly into a configured directory.
    When artifacts are listed, it lists all files in that directory
    recursively, including files that weren't created by the current session.
    """
    def __init__(self, directory: Union[str, Path]):
        self.directory = Path(directory).expanduser().resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def _get_path(self, filename: str, session_id: Optional[str] = None) -> Path:
        # Strip user: prefix if present
        clean_name = filename[5:] if filename.startswith("user:") else filename
        # Clean leading/trailing spaces/slashes
        clean_name = clean_name.strip().lstrip("/\\")
        
        base_dir = self.directory
        if session_id:
            base_dir = ensure_ephemeral_root() / session_id / "output" / "artifacts"
            base_dir.mkdir(parents=True, exist_ok=True)
            
        # Guard against traversal
        resolved = (base_dir / clean_name).resolve()
        try:
            resolved.relative_to(base_dir)
        except ValueError as e:
            raise InputValidationError(f"Invalid artifact filename {filename!r}: escapes storage directory") from e
        return resolved

    async def save_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        artifact: Union[types.Part, dict[str, Any]],
        session_id: Optional[str] = None,
        custom_metadata: Optional[dict[str, Any]] = None,
    ) -> int:
        artifact = ensure_part(artifact)
        filepath = self._get_path(filename, session_id=session_id)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        
        if artifact.inline_data:
            filepath.write_bytes(artifact.inline_data.data)
        elif artifact.text is not None:
            filepath.write_text(artifact.text, encoding="utf-8")
        else:
            raise InputValidationError("Artifact must have either inline_data or text content.")
            
        return 0

    async def load_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: Optional[str] = None,
        version: Optional[int] = None,
    ) -> Optional[types.Part]:
        filepath = self._get_path(filename, session_id=session_id)
        if not filepath.is_file():
            return None
            
        mime_type, _ = mimetypes.guess_type(str(filepath))
        
        # If mime type is text-like, read as text
        if mime_type and (mime_type.startswith("text/") or mime_type in ["application/json", "application/xml", "application/csv"]):
            try:
                text = filepath.read_text(encoding="utf-8")
                return types.Part(text=text)
            except UnicodeDecodeError:
                # Fallback to binary if decoding fails
                pass
                
        data = filepath.read_bytes()
        mime = mime_type or "application/octet-stream"
        return types.Part(inline_data=types.Blob(mime_type=mime, data=data))

    async def list_artifact_keys(
        self, *, app_name: str, user_id: str, session_id: Optional[str] = None
    ) -> list[str]:
        filenames = []
        target_dir = self.directory
        if session_id:
            target_dir = ensure_ephemeral_root() / session_id / "output" / "artifacts"

        if not target_dir.exists():
            return filenames
            
        for root, dirs, files in os.walk(target_dir):
            for file in files:
                filepath = Path(root) / file
                rel_path = filepath.relative_to(target_dir)
                filenames.append(rel_path.as_posix())
                
        return sorted(filenames)

    async def delete_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: Optional[str] = None,
    ) -> None:
        filepath = self._get_path(filename, session_id=session_id)
        if filepath.is_file():
            filepath.unlink()

    async def list_versions(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: Optional[str] = None,
    ) -> list[int]:
        filepath = self._get_path(filename, session_id=session_id)
        if filepath.is_file():
            return [0]
        return []

    async def list_artifact_versions(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: Optional[str] = None,
    ) -> list[ArtifactVersion]:
        filepath = self._get_path(filename, session_id=session_id)
        if not filepath.is_file():
            return []
            
        mime_type, _ = mimetypes.guess_type(str(filepath))
        try:
            create_time = filepath.stat().st_ctime
        except Exception:
            create_time = 0.0
            
        version = ArtifactVersion(
            version=0,
            canonical_uri=filepath.as_uri(),
            create_time=create_time,
            mime_type=mime_type,
        )
        return [version]

    async def get_artifact_version(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: Optional[str] = None,
        version: Optional[int] = None,
    ) -> Optional[ArtifactVersion]:
        filepath = self._get_path(filename, session_id=session_id)
        if not filepath.is_file():
            return None
            
        if version is not None and version != 0:
            return None
            
        mime_type, _ = mimetypes.guess_type(str(filepath))
        try:
            create_time = filepath.stat().st_ctime
        except Exception:
            create_time = 0.0
            
        return ArtifactVersion(
            version=0,
            canonical_uri=filepath.as_uri(),
            create_time=create_time,
            mime_type=mime_type,
        )
