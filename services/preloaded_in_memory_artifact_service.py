import os
import mimetypes
from pathlib import Path
from typing import Optional, Union, Any

from google.adk.artifacts.in_memory_artifact_service import InMemoryArtifactService, _ArtifactEntry
from google.adk.artifacts.base_artifact_service import ArtifactVersion
from google.genai import types

class PreloadedInMemoryArtifactService(InMemoryArtifactService):
    """An in-memory implementation of the artifact service that supports preloading
    artifacts from a local directory (e.g. testing/testdata).
    """

    def preload_directory(self, directory: Union[str, Path], app_name: str = "narratron-combined") -> int:
        """Recursively pre-loads artifacts from a directory into memory."""
        dir_path = Path(directory).resolve()
        if not dir_path.exists():
            return 0
        
        count = 0
        for root, _, files in os.walk(dir_path):
            for file in files:
                file_path = Path(root) / file
                rel_path = file_path.relative_to(dir_path).as_posix()
                
                mime_type, _ = mimetypes.guess_type(str(file_path))
                if mime_type and (mime_type.startswith("text/") or mime_type in ["application/json", "application/xml", "application/csv"]):
                    try:
                        text = file_path.read_text(encoding="utf-8")
                        part = types.Part(text=text)
                    except UnicodeDecodeError:
                        data = file_path.read_bytes()
                        part = types.Part(inline_data=types.Blob(mime_type=mime_type or "application/octet-stream", data=data))
                else:
                    data = file_path.read_bytes()
                    part = types.Part(inline_data=types.Blob(mime_type=mime_type or "application/octet-stream", data=data))
                
                uri = f"memory://apps/{app_name}/artifacts/{rel_path}"
                av = ArtifactVersion(version=0, canonical_uri=uri, mime_type=mime_type)
                entry = _ArtifactEntry(data=part, artifact_version=av)
                
                # Store under both standard relative path and user: namespace path
                for key in [rel_path, f"user:{rel_path}"]:
                    path_key = f"__preloaded__/{key}"
                    if path_key not in self.artifacts:
                        self.artifacts[path_key] = []
                    self.artifacts[path_key].append(entry)
                count += 1
        return count

    async def load_artifact(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: Optional[str] = None,
        version: Optional[int] = None,
    ) -> Optional[types.Part]:
        # Try standard in-memory lookup first
        res = await super().load_artifact(app_name=app_name, user_id=user_id, filename=filename, session_id=session_id, version=version)
        if res is not None:
            return res
        
        # Fallback to preloaded artifacts
        preloaded_key = f"__preloaded__/{filename}"
        entries = self.artifacts.get(preloaded_key)
        if not entries:
            if filename.startswith("user:"):
                preloaded_key = f"__preloaded__/{filename[5:]}"
            else:
                preloaded_key = f"__preloaded__/user:{filename}"
            entries = self.artifacts.get(preloaded_key)
            
        if entries:
            v = version if version is not None else -1
            try:
                return entries[v].data
            except IndexError:
                return None
        return None

    async def list_artifact_keys(
        self, *, app_name: str, user_id: str, session_id: Optional[str] = None
    ) -> list[str]:
        keys = await super().list_artifact_keys(app_name=app_name, user_id=user_id, session_id=session_id)
        preloaded_keys = set()
        for path in self.artifacts:
            if path.startswith("__preloaded__/"):
                k = path.removeprefix("__preloaded__/")
                preloaded_keys.add(k)
        return sorted(list(set(keys).union(preloaded_keys)))

    async def list_versions(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: Optional[str] = None,
    ) -> list[int]:
        res = await super().list_versions(app_name=app_name, user_id=user_id, filename=filename, session_id=session_id)
        if res:
            return res
        preloaded_key = f"__preloaded__/{filename}"
        entries = self.artifacts.get(preloaded_key)
        if entries:
            return list(range(len(entries)))
        return []

    async def list_artifact_versions(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: Optional[str] = None,
    ) -> list[ArtifactVersion]:
        res = await super().list_artifact_versions(app_name=app_name, user_id=user_id, filename=filename, session_id=session_id)
        if res:
            return res
        preloaded_key = f"__preloaded__/{filename}"
        entries = self.artifacts.get(preloaded_key)
        if entries:
            return [entry.artifact_version for entry in entries]
        return []

    async def get_artifact_version(
        self,
        *,
        app_name: str,
        user_id: str,
        filename: str,
        session_id: Optional[str] = None,
        version: Optional[int] = None,
    ) -> Optional[ArtifactVersion]:
        res = await super().get_artifact_version(app_name=app_name, user_id=user_id, filename=filename, session_id=session_id, version=version)
        if res is not None:
            return res
        preloaded_key = f"__preloaded__/{filename}"
        entries = self.artifacts.get(preloaded_key)
        if entries:
            v = version if version is not None else -1
            try:
                return entries[v].artifact_version
            except IndexError:
                return None
        return None
