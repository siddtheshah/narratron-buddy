"""Regression coverage for persisting the canvas music mute preference."""

from api_server.shared import PROJECT_ROOT


def test_music_mute_preference_is_restored_from_local_storage() -> None:
    canvas = (PROJECT_ROOT / "templates" / "canvas.html").read_text(encoding="utf-8")

    assert "const MUSIC_MUTED_STORAGE_KEY = 'narratron_music_muted';" in canvas
    assert "let isMuted = localStorage.getItem(MUSIC_MUTED_STORAGE_KEY) === 'true';" in canvas
    assert "setMusicMuted(isMuted);" in canvas


def test_every_mute_change_is_persisted() -> None:
    canvas = (PROJECT_ROOT / "templates" / "canvas.html").read_text(encoding="utf-8")

    assert "localStorage.setItem(MUSIC_MUTED_STORAGE_KEY, String(muted));" in canvas
    assert "setMusicMuted(!isMuted);" in canvas
    # Raising the volume slider auto-unmutes, which must also be persisted.
    assert "setMusicMuted(false);" in canvas
    assert "isMuted = !isMuted;" not in canvas
