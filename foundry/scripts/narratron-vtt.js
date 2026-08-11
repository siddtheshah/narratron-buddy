/**
 * Narratron Buddy Foundry VTT Integration Module
 */

Hooks.once("init", () => {
  console.log("Narratron Buddy | Initializing Foundry VTT Integration Module");

  game.settings.register("narratron-buddy-vtt", "obsUrl", {
    name: "Narratron OBS URL",
    hint: "Direct URL for the Narratron OBS view. Shared with every player in this world; it may include a theater join key.",
    scope: "world",
    config: true,
    restricted: true,
    type: String,
    default: ""
  });

  game.settings.register("narratron-buddy-vtt", "overlayVisible", {
    name: "Narratron Overlay Visible",
    hint: "Whether Narratron's shared overlay is currently displayed to every connected player.",
    scope: "world",
    config: false,
    restricted: true,
    type: Boolean,
    default: false
  });

  game.settings.register("narratron-buddy-vtt", "enableAudio", {
    name: "Enable Background Audio Sync",
    hint: "Automatically stream audio narration and background ambiance from Narratron in Foundry VTT.",
    scope: "world",
    config: true,
    type: Boolean,
    default: true
  });

  game.settings.register("narratron-buddy-vtt", "audioVolume", {
    name: "Audio Volume",
    hint: "Master volume for Narratron audio playback (0.0 to 1.0).",
    scope: "client",
    config: true,
    type: Number,
    range: { min: 0.0, max: 1.0, step: 0.05 },
    default: 0.8
  });

  game.settings.register("narratron-buddy-vtt", "panelBounds", {
    name: "Narratron Panel Bounds",
    scope: "client",
    config: false,
    type: String,
    default: ""
  });

  game.keybindings.register("narratron-buddy-vtt", "toggleObsOverlay", {
    name: "Toggle Narratron OBS Overlay",
    hint: "Show or hide the Narratron OBS view over the active Foundry canvas.",
    editable: [{ key: "KeyO", modifiers: ["ALT"] }],
    onDown: () => {
      if (game.user.isGM) {
        ui.notifications.info("Narratron: switching OBS overlay…");
        void NarratronObsOverlay.toggle();
      } else {
        NarratronObsOverlay.toggleForPlayer();
      }
      return true;
    },
    restricted: false,
    precedence: CONST.KEYBINDING_PRECEDENCE.NORMAL
  });
});

const MODULE_ID = "narratron-buddy-vtt";

function getObsUrl() {
  return game.settings.get(MODULE_ID, "obsUrl").trim();
}

/** Add the local audio preference without requiring users to edit their OBS URL. */
function getObsUrlWithAudioPreference() {
  const obsUrl = getObsUrl();
  if (!obsUrl) return "";

  try {
    const url = new URL(obsUrl, window.location.href);
    url.searchParams.set(
      "audio",
      game.settings.get(MODULE_ID, "enableAudio") ? "1" : "0"
    );
    url.searchParams.set(
      "audio_volume",
      String(game.settings.get(MODULE_ID, "audioVolume"))
    );
    return url.href;
  } catch (error) {
    console.warn("Narratron Buddy | Could not apply audio preference to OBS URL:", error);
    return obsUrl;
  }
}

/** A floating Narratron panel rendered above Foundry's PIXI canvas. */
class NarratronObsOverlay {
  static playerDismissed = false;
  static boundsSaveTimer = null;

  static get element() {
    return document.getElementById("narratron-obs-overlay");
  }

  static get isVisible() {
    return this.element?.classList.contains("is-visible") ?? false;
  }

  static async toggle() {
    if (!game.user.isGM) return;

    if (!canvas?.ready || !canvas.app?.view) {
      ui.notifications.warn("Narratron OBS overlay is available after a scene canvas has loaded.");
      return;
    }

    if (!getObsUrl()) {
      ui.notifications.warn("Set Narratron OBS URL in Module Settings before opening the overlay.");
      return;
    }

    const shouldShow = !game.settings.get(MODULE_ID, "overlayVisible");
    // Update this client within the originating click/keypress. In particular,
    // this preserves the browser user-activation window needed for iframe audio.
    // Hiding locally first also lets the GM escape an unresponsive iframe.
    if (shouldShow) this.show();
    else this.hide();

    try {
      await game.settings.set(MODULE_ID, "overlayVisible", shouldShow);
    } catch (error) {
      console.error("Narratron Buddy | Could not update shared overlay state:", error);
      ui.notifications.error("Narratron could not update the shared overlay state.");
      if (shouldShow) this.hide();
      else this.show();
    }
  }

  /** Hide or restore this player's view without changing the GM's world state. */
  static toggleForPlayer() {
    if (game.user.isGM) return;

    if (!game.settings.get(MODULE_ID, "overlayVisible")) {
      ui.notifications.info("Narratron is not currently being displayed by the GM.");
      return;
    }

    if (this.isVisible) {
      this.dismissForPlayer();
    } else {
      this.playerDismissed = false;
      this.show();
    }
  }

  static dismissForPlayer() {
    if (game.user.isGM) return;

    this.playerDismissed = true;
    this.hide();
    ui.notifications.info("Narratron display hidden. Press Alt+O to rejoin while the GM is sharing it.");
  }

  static show() {
    if (!canvas?.ready || !canvas.app?.view) {
      if (game.user.isGM) {
        ui.notifications.warn("Narratron OBS overlay is available after a scene canvas has loaded.");
      }
      return;
    }

    const obsUrl = getObsUrlWithAudioPreference();
    if (!obsUrl) {
      if (game.user.isGM) {
        ui.notifications.warn("Set Narratron OBS URL in Module Settings before opening the overlay.");
      }
      return;
    }

    const overlay = this.#create();
    const iframe = overlay.querySelector("iframe");
    console.info("Narratron Buddy | Loading shared OBS overlay URL:", obsUrl);
    if (iframe.dataset.obsUrl !== obsUrl) {
      iframe.src = obsUrl;
      iframe.dataset.obsUrl = obsUrl;
    }
    overlay.classList.add("is-visible");
    this.#setFrameAudio(iframe, "resume-audio");
  }

  static hide() {
    const overlay = this.element;
    this.#setFrameAudio(overlay?.querySelector("iframe"), "pause-audio");
    overlay?.classList.remove("is-visible");
  }

  static handleFrameMessage(event) {
    const iframe = this.element?.querySelector("iframe");
    if (
      event.data?.source !== "narratron-buddy-obs" ||
      event.data?.action !== "hide-overlay" ||
      event.source !== iframe?.contentWindow
    ) return;

    if (game.user.isGM) {
      void this.toggle();
    } else {
      this.dismissForPlayer();
    }
  }

  static #setFrameAudio(iframe, action) {
    iframe?.contentWindow?.postMessage({
      source: "narratron-buddy-foundry",
      action,
    }, "*");
  }

  static #create() {
    let overlay = this.element;
    if (overlay) return overlay;

    overlay = document.createElement("section");
    overlay.id = "narratron-obs-overlay";
    overlay.className = "narratron-obs-overlay";
    overlay.innerHTML = `
      <header class="narratron-obs-titlebar">
        <span class="narratron-obs-title"><i class="fas fa-tv"></i> Narratron</span>
        <button type="button" class="narratron-obs-close" title="Hide Narratron Display (Alt+O)">
          <i class="fas fa-times"></i><span class="sr-only">Hide Narratron Display</span>
        </button>
      </header>
      <iframe class="narratron-obs-iframe" title="Narratron OBS Display & Audio"
        allow="autoplay; microphone; fullscreen; clipboard-write; sound"></iframe>`;
    if (game.user.isGM) {
      overlay.querySelector(".narratron-obs-close").title = "Hide Narratron Display (Alt+O)";
      overlay.insertAdjacentHTML("beforeend", `
        <div class="narratron-obs-shortcut" aria-live="polite">Press <kbd>Alt</kbd>+<kbd>O</kbd> to hide</div>`);
      overlay.querySelector(".narratron-obs-close").addEventListener("click", () => void this.toggle());
    } else {
      overlay.querySelector(".narratron-obs-close").addEventListener("click", () => this.dismissForPlayer());
    }
    overlay.querySelector("iframe").addEventListener("load", (event) => {
      this.#setFrameAudio(
        event.currentTarget,
        this.isVisible ? "resume-audio" : "pause-audio"
      );
    });
    this.#restoreBounds(overlay);
    this.#enableDragging(overlay);
    new ResizeObserver(() => this.#scheduleBoundsSave(overlay)).observe(overlay);
    // #board is Foundry's PIXI <canvas>, which cannot visually host regular
    // HTML children. Mount a sibling panel on <body> instead.
    document.body.appendChild(overlay);
    return overlay;
  }

  static #restoreBounds(overlay) {
    try {
      const saved = JSON.parse(game.settings.get(MODULE_ID, "panelBounds"));
      if (!saved || ![saved.left, saved.top, saved.width, saved.height].every(Number.isFinite)) return;

      const width = Math.min(saved.width, window.innerWidth - 24);
      const height = Math.min(saved.height, window.innerHeight - 24);
      overlay.style.width = `${Math.max(360, width)}px`;
      overlay.style.height = `${Math.max(260, height)}px`;
      overlay.style.left = `${Math.max(12, Math.min(saved.left, window.innerWidth - width - 12))}px`;
      overlay.style.top = `${Math.max(12, Math.min(saved.top, window.innerHeight - height - 12))}px`;
    } catch (error) {
      console.warn("Narratron Buddy | Could not restore panel bounds:", error);
    }
  }

  static #enableDragging(overlay) {
    const titlebar = overlay.querySelector(".narratron-obs-titlebar");
    titlebar.addEventListener("pointerdown", (event) => {
      if (event.target.closest("button")) return;
      const rect = overlay.getBoundingClientRect();
      const offsetX = event.clientX - rect.left;
      const offsetY = event.clientY - rect.top;
      titlebar.setPointerCapture(event.pointerId);

      const move = (moveEvent) => {
        overlay.style.left = `${Math.max(12, Math.min(moveEvent.clientX - offsetX, window.innerWidth - overlay.offsetWidth - 12))}px`;
        overlay.style.top = `${Math.max(12, Math.min(moveEvent.clientY - offsetY, window.innerHeight - overlay.offsetHeight - 12))}px`;
      };
      const stop = () => {
        titlebar.removeEventListener("pointermove", move);
        titlebar.removeEventListener("pointerup", stop);
        titlebar.removeEventListener("pointercancel", stop);
        this.#scheduleBoundsSave(overlay);
      };
      titlebar.addEventListener("pointermove", move);
      titlebar.addEventListener("pointerup", stop);
      titlebar.addEventListener("pointercancel", stop);
    });
  }

  static #scheduleBoundsSave(overlay) {
    clearTimeout(this.boundsSaveTimer);
    this.boundsSaveTimer = setTimeout(() => {
      if (!this.isVisible) return;
      const rect = overlay.getBoundingClientRect();
      void game.settings.set(MODULE_ID, "panelBounds", JSON.stringify({
        left: Math.round(rect.left), top: Math.round(rect.top),
        width: Math.round(rect.width), height: Math.round(rect.height)
      }));
    }, 200);
  }
}

// Keyboard events inside an iframe do not bubble into Foundry. The OBS page
// relays Alt+O with postMessage so the documented shortcut works either side
// of the iframe boundary.
window.addEventListener("message", (event) => NarratronObsOverlay.handleFrameMessage(event));

/** Render the shared world state in this client's local Foundry DOM. */
function synchronizeOverlay() {
  const isSharedOverlayVisible = game.settings.get(MODULE_ID, "overlayVisible");
  if (!isSharedOverlayVisible) {
    NarratronObsOverlay.playerDismissed = false;
  }

  if (isSharedOverlayVisible && !NarratronObsOverlay.playerDismissed && canvas?.ready && canvas.app?.view) {
    NarratronObsOverlay.show();
  } else {
    NarratronObsOverlay.hide();
  }
}

Hooks.once("ready", synchronizeOverlay);
Hooks.on("canvasReady", synchronizeOverlay);
Hooks.on("updateSetting", (setting) => {
  if (
    setting.key === `${MODULE_ID}.overlayVisible` ||
    setting.key === `${MODULE_ID}.obsUrl` ||
    setting.key === `${MODULE_ID}.enableAudio` ||
    setting.key === `${MODULE_ID}.audioVolume`
  ) {
    synchronizeOverlay();
  }
});

Hooks.on("getSceneControlButtons", (controls) => {
  if (!game.user.isGM) return;

  const tokenControls = controls.find(c => c.name === "token") || controls[0];
  if (tokenControls) {
    tokenControls.tools.push({
      name: "narratron-obs",
      title: "Narratron Display & Audio",
      icon: "fas fa-tv",
      visible: true,
      onClick: () => {
        void NarratronObsOverlay.toggle();
      },
      button: true
    });
  }
});
