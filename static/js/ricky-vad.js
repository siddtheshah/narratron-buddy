/**
 * Dedicated @ricky0123/vad-web (Silero VAD) Integration Module
 */

let _vadInstance = null;

/**
 * Initializes and starts @ricky0123/vad-web MicVAD recorder session.
 */
export async function startRickyVadRecorder({
  micStream,
  onSpeechStart,
  onSpeechEnd,
  onFrameProcessed,
  positiveSpeechThreshold,
  negativeSpeechThreshold
}) {
  if (typeof window === "undefined" || !window.vad || !window.vad.MicVAD) {
    throw new Error("@ricky0123/vad-web library (window.vad.MicVAD) is not loaded.");
  }

  const posThreshold = positiveSpeechThreshold !== undefined ? positiveSpeechThreshold : (window.VAD_POSITIVE_THRESHOLD || 0.5);
  const negThreshold = negativeSpeechThreshold !== undefined ? negativeSpeechThreshold : (window.VAD_NEGATIVE_THRESHOLD || Math.max(0.05, posThreshold - 0.15));

  console.log("[RickyVAD] Creating MicVAD instance...", { positiveSpeechThreshold: posThreshold, negativeSpeechThreshold: negThreshold });

  _vadInstance = await window.vad.MicVAD.new({
    stream: micStream,
    positiveSpeechThreshold: posThreshold,
    negativeSpeechThreshold: negThreshold,
    minSpeechFrames: 3,
    redemptionFrames: 8,
    preSpeechPadFrames: 1,
    onSpeechStart: () => {
      console.log("[RickyVAD] Speech START detected");
      if (typeof onSpeechStart === "function") {
        onSpeechStart();
      }
    },
    onSpeechEnd: (audio) => {
      console.log("[RickyVAD] Speech END detected. Audio samples:", audio ? audio.length : 0);
      if (typeof onSpeechEnd === "function") {
        onSpeechEnd(audio);
      }
    },
    onFrameProcessed: (probs, frame) => {
      if (probs && probs.isSpeech !== undefined && typeof window !== "undefined") {
        window.lastVadSpeechProbability = probs.isSpeech;
      }
      if (typeof onFrameProcessed === "function") {
        onFrameProcessed(probs, frame);
      }
    }
  });

  if (typeof window !== "undefined") {
    window.activeVadInstance = _vadInstance;
  }
  _vadInstance.start();
  console.log("[RickyVAD] MicVAD instance started successfully.");
  return _vadInstance;
}

/**
 * Updates positive and negative speech thresholds on active VAD instance.
 */
export function updateRickyVadThresholds(positiveThreshold, negativeThreshold) {
  if (_vadInstance && _vadInstance.options) {
    _vadInstance.options.positiveSpeechThreshold = positiveThreshold;
    _vadInstance.options.negativeSpeechThreshold = negativeThreshold;
    console.log("[RickyVAD] Updated active VAD thresholds:", { positiveThreshold, negativeThreshold });
  }
}

/**
 * Stops and destroys the active @ricky0123/vad-web MicVAD instance.
 */
export function stopRickyVadRecorder() {
  if (_vadInstance) {
    try {
      _vadInstance.pause();
      _vadInstance.destroy();
    } catch (e) {
      console.debug("[RickyVAD] Error destroying VAD instance:", e);
    }
    _vadInstance = null;
    if (typeof window !== "undefined") {
      window.activeVadInstance = null;
    }
    console.log("[RickyVAD] VAD instance stopped and destroyed.");
  }
}
