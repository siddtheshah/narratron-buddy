/**
 * Audio Recorder Worklet
 */

let micStream;
let _lastMicDetection = 0;
let _frameCounter = 0;
let _isSpeaking = false;
let _lastSpeechTime = 0;
const _MIC_DETECT_THRESHOLD = (typeof window !== "undefined" && window.MIC_DETECT_THRESHOLD !== undefined) ? window.MIC_DETECT_THRESHOLD : 0.01; // RMS threshold for "sound detected"
const _MIC_DETECT_THROTTLE_MS = 250; // minimum ms between logs
const _SPEECH_GRACE_PERIOD_MS = 500; // 0.5s grace period before emitting ActivityEnd


export async function startAudioRecorderWorklet(audioRecorderHandler) {
  // Create an AudioContext
  const audioRecorderContext = new AudioContext({ sampleRate: 16000 });
  console.log("AudioContext sample rate:", audioRecorderContext.sampleRate);

  // Load the AudioWorklet module
  const workletURL = new URL("./pcm-recorder-processor.js", import.meta.url);
  await audioRecorderContext.audioWorklet.addModule(workletURL);

  // Request access to the microphone
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { channelCount: 1 },
  });
  const source = audioRecorderContext.createMediaStreamSource(micStream);

  // Create an input-only AudioWorkletNode for the PCMProcessor.
  const audioRecorderNode = new AudioWorkletNode(audioRecorderContext, "pcm-recorder-processor", {
    numberOfInputs: 1,
    numberOfOutputs: 0,
    channelCount: 1,
    channelCountMode: 'explicit',
    channelInterpretation: 'speakers',
  });
  console.log("AudioWorkletNode created:", {
    numberOfInputs: audioRecorderNode.numberOfInputs,
    numberOfOutputs: audioRecorderNode.numberOfOutputs,
    channelCount: audioRecorderNode.channelCount,
    channelCountMode: audioRecorderNode.channelCountMode,
    state: audioRecorderContext.state,
  });

  // Connect the microphone source to the worklet.
  source.connect(audioRecorderNode);
  await audioRecorderContext.resume();
  console.log("AudioContext state after resume:", audioRecorderContext.state);

  audioRecorderNode.port.onmessage = (event) => {
    // event.data is a Float32Array of samples.
    // Compute RMS to detect when microphone input is present.
    try {
      const samples = event.data;
      let sum = 0.0;
      for (let i = 0; i < samples.length; i++) {
        sum += samples[i] * samples[i];
      }
      const rms = Math.sqrt(sum / samples.length);
      _frameCounter += 1;
      if (_frameCounter % 20 === 0) {
        console.log("Audio frame RMS:", rms.toFixed(6), "samples=", samples.length, "isSpeaking=", _isSpeaking);
      }
      const now = Date.now();
      if (rms > _MIC_DETECT_THRESHOLD) {
        _lastSpeechTime = now;
        if (!_isSpeaking) {
          _isSpeaking = true;
          console.log("[AudioRecorder] Activity START detected (RMS:", rms.toFixed(5), ")");
          try {
            const ws = window.agentWs;
            if (ws && ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ type: "activity_start", ts: new Date().toISOString() }));
            }
          } catch (e) {
            console.debug("Failed to send activity_start:", e);
          }
        }
        if (now - _lastMicDetection > _MIC_DETECT_THROTTLE_MS) {
          _lastMicDetection = now;
          const ts = new Date().toISOString();
          try {
            const ws = window.agentWs;
            if (ws && ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ type: "mic_detect", rms: rms, ts }));
            }
          } catch (e) {
            console.debug("Failed to send mic_detect over agentWs:", e);
          }
        }
      } else if (_isSpeaking && (now - _lastSpeechTime > _SPEECH_GRACE_PERIOD_MS)) {
        _isSpeaking = false;
        console.log("[AudioRecorder] Activity END detected (Grace period expired)");
        try {
          const ws = window.agentWs;
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "activity_end", ts: new Date().toISOString() }));
          }
        } catch (e) {
          console.debug("Failed to send activity_end:", e);
        }
      }
    } catch (err) {
      console.warn("Failed to compute mic RMS:", err);
    }

    // Only forward PCM audio chunks when speaking or within grace period
    const now = Date.now();
    const isWithinGracePeriod = (now - _lastSpeechTime <= _SPEECH_GRACE_PERIOD_MS);
    if (_isSpeaking || isWithinGracePeriod) {
      const pcmData = convertFloat32ToPCM(event.data);
      try {
        if (typeof audioRecorderHandler === 'function') {
          audioRecorderHandler(pcmData);
        } else {
          console.debug('audioRecorderHandler not a function, skipping send');
        }
      } catch (err) {
        console.warn('audioRecorderHandler threw an error:', err);
      }
    }
  };
  return [audioRecorderNode, audioRecorderContext, micStream];
}

/**
 * Stop the microphone.
 */
export function stopMicrophone(micStream) {
  if (_isSpeaking) {
    _isSpeaking = false;
    try {
      const ws = window.agentWs;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "activity_end", ts: new Date().toISOString() }));
      }
    } catch (e) {
      console.debug("Failed to send activity_end on stopMicrophone:", e);
    }
  }
  micStream.getTracks().forEach((track) => track.stop());
  console.log("stopMicrophone(): Microphone stopped.");
}

// Convert Float32 samples to 16-bit PCM.
function convertFloat32ToPCM(inputData) {
  // Create an Int16Array of the same length.
  const pcm16 = new Int16Array(inputData.length);
  for (let i = 0; i < inputData.length; i++) {
    // Multiply by 0x7fff (32767) to scale the float value to 16-bit PCM range.
    pcm16[i] = inputData[i] * 0x7fff;
  }
  // Return the underlying ArrayBuffer.
  return pcm16.buffer;
}
