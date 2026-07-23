"""Global OS Hotkey Listener for Narratron Orator Mic Toggle.

Listens for system-wide hotkeys configured in config.yaml even when the browser is unfocused or minimized,
and triggers the Narratron backend API to toggle the microphone on all active Orator canvas windows.
"""

import json
import logging
from pathlib import Path
import sys
import time
import urllib.request

from utils.config_loader import get_config

logger = logging.getLogger(__name__)

config = get_config()
orator_cfg = config.get("orator", {})
SERVER_URL = orator_cfg.get("server_url", "http://127.0.0.1:8000/api/orator/toggle_mic")
HOTKEY_COMBO = orator_cfg.get("hotkey", "<ctrl>+<shift>+[")

def trigger_mic_toggle():
    try:
        req = urllib.request.Request(SERVER_URL, method="POST")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"[{time.strftime('%H:%M:%S')}] 🎙️ Global Hotkey Triggered! Response: {data}")
    except Exception as e:
        logger.error(f"Failed to send mic toggle signal: {e}")

def main():
    display_hotkey = HOTKEY_COMBO.replace("<ctrl>", "Ctrl").replace("<shift>", "Shift").replace("<alt>", "Alt").replace("+", " + ")
    print("=" * 60)
    print(" Narratron Global OS Hotkey Listener")
    print(" Target API:", SERVER_URL)
    print(" Hotkey:", display_hotkey)
    print("=" * 60)

    try:
        from pynput import keyboard
    except ImportError:
        print("\n[Notice] 'pynput' library is not installed.")
        print("To enable background OS-level hotkeys when the browser is out of focus:")
        print("    pip install pynput")
        print("\nPressing Enter now will send a test toggle trigger to the server...")
        input()
        trigger_mic_toggle()
        return

    def on_activate():
        trigger_mic_toggle()

    try:
        with keyboard.GlobalHotKeys({
            HOTKEY_COMBO: on_activate
        }) as h:
            print(f"\nListening for {display_hotkey} globally... (Press Ctrl+C to exit)")
            h.join()
    except Exception as e:
        logger.error(f"Error initializing hotkey '{HOTKEY_COMBO}': {e}")
        print("Please check the 'orator.hotkey' setting in config.yaml.")

if __name__ == "__main__":
    main()
