/**
 * Narratron Buddy Foundry VTT Integration Module
 */

Hooks.once("init", () => {
  console.log("Narratron Buddy | Initializing Foundry VTT Integration Module");

  game.settings.register("narratron-buddy-vtt", "serverUrl", {
    name: "Narratron Server URL",
    hint: "Base URL where Narratron API server is running (e.g. http://localhost:8000)",
    scope: "world",
    config: true,
    type: String,
    default: "http://localhost:8000"
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
});

class NarratronObsWindow extends Application {
  static get defaultOptions() {
    return mergeObject(super.defaultOptions, {
      id: "narratron-obs-window",
      title: "Narratron OBS Display & Audio Canvas",
      template: "modules/narratron-buddy-vtt/templates/obs-frame.hbs",
      width: 1024,
      height: 600,
      resizable: true,
      popOut: true,
      minimizable: true
    });
  }

  getData() {
    const baseUrl = game.settings.get("narratron-buddy-vtt", "serverUrl").replace(/\/$/, "");
    return {
      obsUrl: `${baseUrl}/obs`
    };
  }
}

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
        const existing = Object.values(ui.windows).find(w => w.id === "narratron-obs-window");
        if (existing) {
          existing.close();
        } else {
          new NarratronObsWindow().render(true);
        }
      },
      button: true
    });
  }
});

