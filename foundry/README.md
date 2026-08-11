# Narratron Buddy - Foundry VTT Module

This folder contains the official Foundry VTT module for Narratron Buddy. It adds a GM control and keybinding that display Narratron's OBS Canvas view (`/obs`) directly over the Foundry scene canvas for every connected player.

## Installation into Foundry VTT

1. Copy or symlink this `foundry/` directory into your Foundry VTT User Data directory under `Data/modules/narratron-buddy-vtt`:
   - **Windows**: `%LOCALAPPDATA%\FoundryVTT\Data\modules\narratron-buddy-vtt`
   - **macOS**: `~/Library/Application Support/FoundryVTT/Data/modules/narratron-buddy-vtt`
   - **Linux**: `~/.local/share/FoundryVTT/Data/modules/narratron-buddy-vtt`

2. Launch or restart Foundry VTT.
3. In Foundry VTT, go to **Game Settings** -> **Manage Modules**, find **Narratron Buddy Integration**, and enable it.
4. In **Module Settings**, as GM, set the shared Narratron OBS URL.
5. Open any scene in your world. As the GM, you will see a TV icon (`fas fa-tv`) in the left-side Scene Controls panel (under Token controls).
6. Click the TV icon, or press **Alt+O**, to toggle the Narratron OBS canvas overlay for every connected player. The keybinding can be changed in **Configure Controls**.

## Configuration & Audio Settings

In Foundry VTT: **Game Settings** -> **Configure Settings** -> **Module Settings**:
- **Narratron OBS URL**: Set the full direct URL for the OBS view. This is a GM-managed world setting and is loaded by every player, so it may include a theater join key only when your world users are trusted. The URL must be reachable from every player's browser; do not use `localhost` unless every player is running Narratron locally.
- **Enable Background Audio Sync**: Includes Narratron's selected background playlist in the OBS view embedded by Foundry VTT. The setting is enabled by default.
- **Audio Volume**: Controls the Narratron playlist volume in the Foundry overlay (default: `0.8`).

The GM controls whether the overlay is visible. Connected players render their own local instance automatically, and players joining while it is enabled also see it. Players can hide their own view with its close button (or **Alt+O**) without affecting anyone else, and use **Alt+O** to rejoin while the GM is still sharing it. After upgrading from a prior version, enter the OBS URL again because it has moved from a client setting to a world setting.

Hiding the overlay pauses Narratron’s embedded music; reopening it resumes the active playlist.

## Streaming audio

The `/obs` page now plays the active Narratron background playlist as well as rendering the visuals. In OBS, enable audio capture for its Narratron Browser Source (the exact option name varies by OBS version) so the page audio is included in the stream/mixer. The page has no player UI. When previewing it in a normal browser, its autoplay policy may require one click on the canvas before sound can begin; OBS Browser Source normally permits autoplay. The Foundry overlay passes its **Enable Background Audio Sync** setting to the same page; set it off if music should stay out of the Foundry client.
