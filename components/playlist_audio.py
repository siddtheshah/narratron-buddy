"""Validate playlist audio tracks before accepting or serving uploaded bytes."""

from pathlib import Path
from typing import Callable


_DISGUISED_MARKUP_MARKERS: tuple[bytes, ...] = (
    b"<!doctype",
    b"<html",
    b"<head",
    b"<body",
    b"<script",
    b"<svg",
    b"<?xml",
    b"<iframe",
)


def _is_mp3(content: bytes) -> bool:
    if len(content) < 4:
        return False
    if content.startswith(b"ID3"):
        return True
    b0, b1, b2 = content[0], content[1], content[2]
    return (
        b0 == 0xFF
        and (b1 & 0xE0) == 0xE0
        and (b1 & 0x18) != 0x08
        and (b1 & 0x06) != 0x00
        and (b2 & 0xF0) != 0xF0
        and (b2 & 0x0C) != 0x0C
    )


def _is_wav(content: bytes) -> bool:
    return len(content) >= 12 and content[:4] in (b"RIFF", b"RF64") and content[8:12] == b"WAVE"


def _is_ogg(content: bytes) -> bool:
    return len(content) >= 4 and content.startswith(b"OggS")


def _is_flac(content: bytes) -> bool:
    return len(content) >= 4 and content.startswith((b"fLaC", b"ID3"))


def _is_m4a(content: bytes) -> bool:
    return len(content) >= 12 and content[4:8] == b"ftyp"


def _is_aac(content: bytes) -> bool:
    if len(content) < 4:
        return False
    if content.startswith((b"ADIF", b"ID3")):
        return True
    if len(content) >= 12 and content[4:8] == b"ftyp":
        return True
    b0, b1 = content[0], content[1]
    return b0 == 0xFF and (b1 & 0xF0) == 0xF0 and (b1 & 0x06) == 0x00


PLAYLIST_AUDIO_FORMATS: dict[str, tuple[Callable[[bytes], bool], str]] = {
    ".mp3": (_is_mp3, "audio/mpeg"),
    ".wav": (_is_wav, "audio/wav"),
    ".ogg": (_is_ogg, "audio/ogg"),
    ".flac": (_is_flac, "audio/flac"),
    ".m4a": (_is_m4a, "audio/mp4"),
    ".aac": (_is_aac, "audio/aac"),
}


def playlist_audio_type(filename: str, content: bytes) -> str:
    """Require a supported audio extension and matching binary container header."""
    expected = PLAYLIST_AUDIO_FORMATS.get(Path(filename).suffix.lower())
    if expected is None:
        raise ValueError("Playlist tracks must be MP3, WAV, OGG, FLAC, M4A, or AAC audio files.")
    validator, media_type = expected
    sniff_window = content[:512].lstrip().lower()
    if any(marker in sniff_window for marker in _DISGUISED_MARKUP_MARKERS):
        raise ValueError("Playlist track must contain valid audio data.")
    if not validator(content):
        raise ValueError("Playlist track content does not match its audio extension.")
    return media_type
