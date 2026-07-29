/**
 * Audio Recorder Worklet
 */

let micStream;
let _lastMicDetection = 0;
let _frameCounter = 0;
const _MIC_DETECT_THRESHOLD = 0.0005; // RMS threshold for "sound detected"
const _MIC_DETECT_THROTTLE_MS = 250; // minimum ms between logs

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
    console.log("EVENT WAS RECEIVED FROM AUDIO WORKLET.");
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
        console.log("Audio frame RMS:", rms.toFixed(6), "samples=", samples.length);
      }
      const now = Date.now();
      if (rms > _MIC_DETECT_THRESHOLD && now - _lastMicDetection > _MIC_DETECT_THROTTLE_MS) {
        _lastMicDetection = now;
        const ts = new Date().toISOString();
        console.log("Microphone input detected (RMS):", rms.toFixed(5), ts);
        // Send a mic-detection event over the agent WebSocket if available.
        try {
          const ws = window.agentWs;
          if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify({ type: "mic_detect", rms: rms, ts }));
          } else {
            console.debug('agentWs not open, skipping mic_detect send');
          }
        } catch (e) {
          // Non-fatal if websocket not available
          console.debug("Failed to send mic_detect over agentWs:", e);
        }
      }
    } catch (err) {
      console.warn("Failed to compute mic RMS:", err);
    }

    // Convert to 16-bit PCM and forward to handler.
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
  };
  return [audioRecorderNode, audioRecorderContext, micStream];
}

/**
 * Stop the microphone.
 */
export function stopMicrophone(micStream) {
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
