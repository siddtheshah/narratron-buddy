# Narratron Buddy - Foundry VTT Module

This folder contains the official Foundry VTT module for Narratron Buddy. It adds a GM control and keybinding that display Narratron's OBS Canvas view (`/obs`) directly over the Foundry scene canvas.

## Installation into Foundry VTT

1. Copy or symlink this `foundry/` directory into your Foundry VTT User Data directory under `Data/modules/narratron-buddy-vtt`:
   - **Windows**: `%LOCALAPPDATA%\FoundryVTT\Data\modules\narratron-buddy-vtt`
   - **macOS**: `~/Library/Application Support/FoundryVTT/Data/modules/narratron-buddy-vtt`
   - **Linux**: `~/.local/share/FoundryVTT/Data/modules/narratron-buddy-vtt`

2. Launch or restart Foundry VTT.
3. In Foundry VTT, go to **Game Settings** -> **Manage Modules**, find **Narratron Buddy Integration**, and enable it.
4. Open any scene in your world. As the GM, you will see a TV icon (`fas fa-tv`) in the left-side Scene Controls panel (under Token controls).
5. Click the TV icon, or press **Alt+O**, to toggle the Narratron OBS canvas overlay. The keybinding can be changed in **Configure Controls**.

## Configuration & Audio Settings

In Foundry VTT: **Game Settings** -> **Configure Settings** -> **Module Settings**:
- **Narratron OBS URL**: Set the full direct URL for the OBS view, for example `http://localhost:8000/obs?theater_id=...`. This client-only setting may include a theater join key.
- **Enable Background Audio Sync**: Includes Narratron's selected background playlist in the OBS view embedded by Foundry VTT. The setting is enabled by default.
- **Audio Volume**: Controls the Narratron playlist volume in the Foundry overlay (default: `0.8`).

Hiding the overlay pauses Narratron’s embedded music; reopening it resumes the active playlist.

## Streaming audio

The `/obs` page now plays the active Narratron background playlist as well as rendering the visuals. In OBS, enable audio capture for its Narratron Browser Source (the exact option name varies by OBS version) so the page audio is included in the stream/mixer. The page has no player UI. When previewing it in a normal browser, its autoplay policy may require one click on the canvas before sound can begin; OBS Browser Source normally permits autoplay. The Foundry overlay passes its **Enable Background Audio Sync** setting to the same page; set it off if music should stay out of the Foundry client.
