/** A small record-pcm-compatible VAD capture helper with explicit device selection. */
export async function listenForSpeech(options) {
  const { deviceId, onData, onError = () => {}, onSpeechStart = () => {}, onSpeechEnd = () => {}, sampleRate = 16000, vadThreshold = 0.01, vadSilenceDuration = 600, vadMinRecordingTime = 180, continuous = true } = options;
  let listening = true, speaking = false, context, stream, source, worklet, silenceStart = null, speechStart = null;
  const cleanup = () => { listening = false; worklet?.disconnect(); source?.disconnect(); if (context?.state !== "closed") context.close(); stream?.getTracks().forEach((track) => track.stop()); };
  const stop = () => { if (!listening) return; if (speaking) onSpeechEnd(); cleanup(); };
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { ...(deviceId ? { deviceId: { exact: deviceId } } : {}), channelCount: 1, sampleRate: { ideal: sampleRate }, echoCancellation: true, noiseSuppression: true } });
    if (!listening) return cleanup();
    context = new AudioContext({ sampleRate });
    // 480 samples = 30 ms at the 16 kHz capture rate.  This lets each PCM
    // frame reach the Live API immediately instead of arriving in bursts.
    const processor = `class P extends AudioWorkletProcessor { constructor(){super();this.b=new Float32Array(480);this.i=0} process(inputs){const a=inputs[0]?.[0];if(!a)return true;for(const s of a){this.b[this.i++]=s;if(this.i===this.b.length){let q=0;for(const x of this.b)q+=x*x;const p=new Int16Array(this.b.length);for(let j=0;j<this.b.length;j++){const x=Math.max(-1,Math.min(1,this.b[j]));p[j]=x<0?x*32768:x*32767}this.port.postMessage({pcm:new Uint8Array(p.buffer),float32:this.b.slice(),rms:Math.sqrt(q/this.b.length)});this.i=0}}return true} } registerProcessor('narratron-device-pcm',P);`;
    const url = URL.createObjectURL(new Blob([processor], { type: "application/javascript" }));
    await context.audioWorklet.addModule(url); URL.revokeObjectURL(url);
    if (!listening) return cleanup();
    source = context.createMediaStreamSource(stream); worklet = new AudioWorkletNode(context, "narratron-device-pcm");
    worklet.port.onmessage = ({ data: { pcm, float32, rms } }) => {
      if (!listening || !pcm?.length) return;
      if (rms >= vadThreshold) { silenceStart = null; if (!speaking) { speaking = true; speechStart = Date.now(); onSpeechStart(); } onData({ pcm, float32, rms }); return; }
      if (!speaking) return;
      onData({ pcm, float32, rms }); silenceStart ??= Date.now();
      if (Date.now() - speechStart > vadMinRecordingTime && Date.now() - silenceStart > vadSilenceDuration) { speaking = false; speechStart = null; silenceStart = null; onSpeechEnd(); if (!continuous) cleanup(); }
    };
    source.connect(worklet); worklet.connect(context.destination);
    return stop;
  } catch (error) { cleanup(); onError(error instanceof Error ? error : new Error(String(error))); throw error; }
}
