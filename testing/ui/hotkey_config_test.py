from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_canvas_contains_configurable_text_hotkey_controls():
    content = (PROJECT_ROOT / "templates" / "canvas.html").read_text(encoding="utf-8")

    # Check button and display elements exist under microphone controls modal
    assert 'id="menu-item-text-hotkey"' in content
    assert 'id="menu-text-hotkey-display"' in content
    assert "Text Input Hotkey: Shift + Space" in content
    assert "Click to rebind Orator text input hotkey" in content

    # Verify menu-item-text-hotkey is in mic-config-modal alongside menu-item-hotkey
    mic_modal_snippet = content.split('id="mic-config-modal"', 1)[1].split('id="mic-config-done-btn"', 1)[0]
    assert 'id="menu-item-hotkey"' in mic_modal_snippet
    assert 'id="menu-item-text-hotkey"' in mic_modal_snippet


def test_canvas_contains_text_hotkey_rebinding_and_storage_logic():
    content = (PROJECT_ROOT / "templates" / "canvas.html").read_text(encoding="utf-8")

    # Storage key for text hotkey preference
    assert "narratron_text_hotkey" in content
    assert "isRebindingTextHotkey" in content
    assert "startTextHotkeyRebind" in content
    assert "updateTextHotkeyUI" in content

    # Default hotkey: Space + Shift
    assert "code: 'Space'" in content
    assert "shiftKey: true" in content

    # Verification helper exposed on window
    assert "window.setTextHotkey" in content
    assert "window.setTextCommandHotkey" in content

    # Triggering openOratorCommand on matching hotkey
    assert "matchTextCode" in content
    assert "openOratorCommand();" in content


def test_orator_howto_modal_documents_text_input_and_configuration():
    content = (PROJECT_ROOT / "templates" / "canvas.html").read_text(encoding="utf-8")

    howto_snippet = content.split('id="orator-howto-modal"', 1)[1].split('id="howto-ack-btn"', 1)[0]

    # Documents text input mode
    assert "Command Narratron via Text" in howto_snippet or "Text Input" in howto_snippet
    assert 'id="howto-text-hotkey-display"' in howto_snippet

    # Documents how to customize hotkeys in Microphone Configuration
    assert "Microphone Configuration" in howto_snippet
    assert 'id="howto-tip-text-hotkey"' in howto_snippet
