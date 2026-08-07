# Narratron Buddy - Foundry VTT Module

This folder contains the official Foundry VTT module for Narratron Buddy. It adds a GM panel trigger in Foundry VTT to display Narratron's OBS Canvas view (`/obs`) inside a floating application window.

## Installation into Foundry VTT

1. Copy or symlink this `foundry/` directory into your Foundry VTT User Data directory under `Data/modules/narratron-buddy-vtt`:
   - **Windows**: `%LOCALAPPDATA%\FoundryVTT\Data\modules\narratron-buddy-vtt`
   - **macOS**: `~/Library/Application Support/FoundryVTT/Data/modules/narratron-buddy-vtt`
   - **Linux**: `~/.local/share/FoundryVTT/Data/modules/narratron-buddy-vtt`

2. Launch or restart Foundry VTT.
3. In Foundry VTT, go to **Game Settings** -> **Manage Modules**, find **Narratron Buddy Integration**, and enable it.
4. Open any scene in your world. As the GM, you will see a TV icon (`fas fa-tv`) in the left-side Scene Controls panel (under Token controls).
5. Click the TV icon to toggle open the Narratron OBS Canvas pop-out window.

## Configuration & Audio Settings

In Foundry VTT: **Game Settings** -> **Configure Settings** -> **Module Settings**:
- **Narratron Server URL**: Set to your Narratron server host (default: `http://localhost:8000`).
- **Enable Background Audio Sync**: Toggles automatic audio stream/narration sync from Narratron into Foundry VTT.
- **Audio Volume**: Controls master volume level for Narratron audio playback (default: `0.8`).
