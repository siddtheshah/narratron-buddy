"""Shared utility functions for resolving and attaching character references for tools.

Consolidated into components.reference_manager; re-exported here for compatibility.
"""

from __future__ import annotations

from components.reference_manager import (
    CharacterLookupResult,
    ImagePathResolver,
    ReferenceManager,
    get_reference_label,
)
from providers import ImageReference

__all__ = [
    "CharacterLookupResult",
    "ImagePathResolver",
    "ImageReference",
    "ReferenceManager",
    "get_reference_label",
]
