"""Shared utility functions for resolving and attaching character references for tools."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import re
from typing import Optional, Protocol, Union

from components.character_manager import CharacterLookupResult, CharacterManager
from providers import ImageReference

logger = logging.getLogger(__name__)


class ImagePathResolver(Protocol):
    """Protocol for resolving image names or aliases to filesystem paths."""

    def resolve_image_path(self, query: str) -> Optional[str]: ...


def is_character_reference(
    ref: str,
    character_manager: Optional[CharacterManager] = None,
    resolved_path: Optional[str] = None,
    lookup_result: Optional[CharacterLookupResult] = None,
) -> bool:
    """Return True if ref is identified as a character reference."""
    if character_manager is None:
        return False

    ref_clean = str(ref).strip()
    if not ref_clean:
        return False
    ref_norm = ref_clean.lower()
    ref_stem = Path(ref_clean).stem.lower()

    # 1. Check against acquired characters and player from lookup_result
    if lookup_result is not None:
        for char in lookup_result.characters:
            char_name = char.name.strip().lower()
            char_alias = char.alias.strip().lower()
            char_img_ref = (char.image_reference or "").strip().lower()
            char_path = (char.image_reference_path or "").strip().lower()
            if ref_norm in (char_name, char_alias, char_img_ref, char_path):
                return True
            if ref_stem in (char_alias, Path(char_img_ref).stem.lower(), Path(char_path).stem.lower()):
                return True
            if len(char_name) >= 2:
                ref_words = ref_norm.replace("_", " ").replace("-", " ")
                if re.search(r"\b" + re.escape(char_name) + r"\b", ref_words):
                    return True

        if lookup_result.player is not None:
            player = lookup_result.player
            player_name = (player.name or "").strip().lower()
            player_ref = (player.reference or "").strip().lower()
            player_path = (player.reference_path or "").strip().lower()
            if ref_norm in (player_name, player_ref, player_path):
                return True
            if ref_stem in (Path(player_ref).stem.lower(), Path(player_path).stem.lower()):
                return True
            if len(player_name) >= 2:
                ref_words = ref_norm.replace("_", " ").replace("-", " ")
                if re.search(r"\b" + re.escape(player_name) + r"\b", ref_words):
                    return True

    # 2. Check against all known character references from character_manager
    all_char_refs = character_manager.get_character_references()
    if type(all_char_refs) is list:
        for c_ref in all_char_refs:
            c_clean = str(c_ref).strip().lower()
            if ref_norm == c_clean or ref_stem == Path(c_clean).stem.lower():
                return True
            if resolved_path is not None and os.path.isabs(c_clean):
                norm_c = os.path.normcase(os.path.abspath(c_clean))
                norm_p = os.path.normcase(os.path.abspath(resolved_path))
                if norm_c == norm_p:
                    return True

    # 3. Check character directory or parent directory matching character name
    for target in (ref_clean, resolved_path or ""):
        if not target:
            continue
        p = Path(target)
        if "characters" in [part.lower() for part in p.parts]:
            return True
        parent_name = p.parent.name
        if parent_name and parent_name.lower() not in ("references", "images", "artifacts", "output", "characters", "."):
            res = character_manager.get_latest_reference_path_for_character(parent_name)
            if type(res) is str and res.strip():
                return True

    # 4. Check explicit character naming tags in filename
    if re.search(r"(^|[_-])(character|player_character|portrait)($|[._-])", ref_norm):
        return True

    return False


def get_reference_label(
    ref: str,
    resolved_path: Optional[str] = None,
    lookup_result: Optional[CharacterLookupResult] = None,
) -> str:
    """Return a descriptive label for a reference (e.g., character name or clean identifier)."""
    ref_clean = str(ref).strip()
    ref_norm = ref_clean.lower()
    ref_stem = Path(ref_clean).stem.lower()
    resolved_norm = os.path.normcase(os.path.abspath(resolved_path)) if resolved_path else None

    # 1. Check against acquired characters and player from lookup_result
    if lookup_result is not None:
        if lookup_result.player is not None:
            player = lookup_result.player
            player_name = (player.name or "").strip()
            player_ref = (player.reference or "").strip().lower()
            player_path = (player.reference_path or "").strip().lower()
            norm_player_path = os.path.normcase(os.path.abspath(player.reference_path)) if player.reference_path else None

            if (
                ref_norm in (player_name.lower(), player_ref, player_path)
                or ref_stem in (Path(player_ref).stem.lower(), Path(player_path).stem.lower())
                or (resolved_norm is not None and resolved_norm == norm_player_path)
            ):
                return player_name or "Player"
            if len(player_name) >= 2:
                ref_words = ref_norm.replace("_", " ").replace("-", " ")
                if re.search(r"\b" + re.escape(player_name.lower()) + r"\b", ref_words):
                    return player_name

        for char in lookup_result.characters:
            char_name = char.name.strip()
            char_alias = char.alias.strip().lower()
            char_img_ref = (char.image_reference or "").strip().lower()
            char_path = (char.image_reference_path or "").strip().lower()
            norm_char_path = os.path.normcase(os.path.abspath(char.image_reference_path)) if char.image_reference_path else None
            parent_norm = os.path.normcase(Path(resolved_path).parent.name) if resolved_path else ""

            if (
                ref_norm in (char_name.lower(), char_alias, char_img_ref, char_path)
                or ref_stem in (char_alias, Path(char_img_ref).stem.lower(), Path(char_path).stem.lower())
                or (resolved_norm is not None and resolved_norm == norm_char_path)
                or (parent_norm and parent_norm in (char_name.lower(), char_alias))
            ):
                return char_name
            if len(char_name) >= 2:
                ref_words = ref_norm.replace("_", " ").replace("-", " ")
                if re.search(r"\b" + re.escape(char_name.lower()) + r"\b", ref_words):
                    return char_name

    # 2. Fallback to character directory name if iteration number, or filename stem
    if resolved_path is not None:
        stem = Path(resolved_path).stem
        parent_name = Path(resolved_path).parent.name
        if (stem.isdigit() or stem.startswith("iteration")) and parent_name and parent_name.lower() not in ("references", "images", "artifacts", "output", "."):
            return parent_name
        return stem
    return Path(ref_clean).stem or ref_clean


def resolve_provider_references(
    reference_images: Union[list[str], str, None],
    prompt: str = "",
    character_manager: Optional[CharacterManager] = None,
    visual: Optional[ImagePathResolver] = None,
    caller_label: str = "Tool",
) -> tuple[list[ImageReference], Optional[str]]:
    """Resolve and attach references from character_manager and caller reference images.

    Handles character reference lookup from prompt, caller overrides, deduplication,
    path verification, and loading bytes into ImageReference objects.

    Returns:
        tuple of (list of resolved ImageReference objects, error message if any or None).
    """
    char_resolved_refs: list[tuple[str, str]] = []
    char_seen_keys: set[str] = set()
    char_seen_paths: set[str] = set()
    lookup_result: Optional[CharacterLookupResult] = None

    if character_manager is not None:
        lookup_result = character_manager.lookup_character(prompt, name_only=True)
        for ref in lookup_result.get_character_references():
            ref_clean = str(ref).strip()
            ref_key = ref_clean.casefold()
            if not ref_key or ref_key in char_seen_keys:
                continue
            ref_path = None
            if os.path.isfile(ref_clean):
                ref_path = ref_clean
            elif character_manager is not None:
                res = character_manager.get_latest_reference_path_for_character(ref_clean)
                if type(res) is str and res.strip():
                    ref_path = res.strip()
            if ref_path is None and visual is not None:
                ref_path = visual.resolve_image_path(ref_clean)
            if ref_path is not None:
                norm_path = os.path.normcase(os.path.abspath(ref_path))
                if norm_path not in char_seen_paths:
                    char_seen_keys.add(ref_key)
                    char_seen_paths.add(norm_path)
                    char_resolved_refs.append((ref_clean, ref_path))
            else:
                logger.debug(f"[{caller_label}] Character reference '{ref_clean}' could not be resolved; skipping.")

    resolved_refs: list[tuple[str, str]] = list(char_resolved_refs)
    seen_keys: set[str] = set(char_seen_keys)
    seen_paths: set[str] = set(char_seen_paths)

    if reference_images is not None:
        if type(reference_images) is str:
            ref_list = [r.strip() for r in reference_images.split(",") if r.strip()]
        else:
            ref_list = [str(r).strip() for r in reference_images if str(r).strip()]

        for ref in ref_list:
            ref_key = ref.casefold()

            latest_char_path: Optional[str] = None
            if character_manager is not None:
                res = character_manager.get_latest_reference_path_for_character(ref)
                if type(res) is str and res.strip():
                    latest_char_path = res.strip()

            ref_path = latest_char_path
            if ref_path is None and visual is not None:
                ref_path = visual.resolve_image_path(ref)

            if ref_path is None:
                if char_resolved_refs and is_character_reference(
                    ref,
                    character_manager=character_manager,
                    lookup_result=lookup_result,
                ):
                    continue
                logger.error(f"[{caller_label}] Reference image '{ref}' not found.")
                return [], f"Error: Reference image '{ref}' not found."

            norm_path = os.path.normcase(os.path.abspath(ref_path))
            if norm_path in seen_paths or ref_key in seen_keys:
                continue

            if char_resolved_refs and is_character_reference(
                ref,
                character_manager=character_manager,
                resolved_path=ref_path,
                lookup_result=lookup_result,
            ):
                logger.debug(f"[{caller_label}] Caller character reference '{ref}' overridden by character_manager.")
                continue

            seen_keys.add(ref_key)
            seen_paths.add(norm_path)
            resolved_refs.append((ref, ref_path))

    provider_references: list[ImageReference] = []
    for ref_name, reference_path in resolved_refs:
        try:
            data = Path(reference_path).read_bytes()
        except OSError as exc:
            logger.error(f"[{caller_label}] Error loading reference image {reference_path}: {exc}")
            return [], f"Error loading reference image '{ref_name}': {exc}"
        except Exception as exc:
            logger.error(f"[{caller_label}] Error loading reference image {reference_path}: {exc}")
            return [], f"Error loading reference image '{ref_name}': {exc}"

        suffix = Path(reference_path).suffix.lower()
        mime_type = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
        label = get_reference_label(
            ref=ref_name,
            resolved_path=reference_path,
            lookup_result=lookup_result,
        )
        provider_references.append(
            ImageReference(
                name=Path(reference_path).name,
                data=data,
                mime_type=mime_type,
                label=label,
            )
        )

    if provider_references:
        logger.debug(f"[{caller_label}] Adapted prompt with {len(provider_references)} reference images by bytes.")
        logger.debug(f"[{caller_label}] provider references: {[p.name for p in provider_references]}")

    return provider_references, None
