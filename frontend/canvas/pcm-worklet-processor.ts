declare class AudioWorkletProcessor {
  readonly port: MessagePort;
  process(
    inputs: readonly (readonly Float32Array[])[],
    outputs: readonly (readonly Float32Array[])[],
    parameters: Readonly<Record<string, Float32Array>>,
  ): boolean;
}

declare function registerProcessor(
  name: string,
  processorCtor: new () => AudioWorkletProcessor,
): void;

declare const sampleRate: number;

export type PcmWorkletPayloadMode = 'pcm-vad' | 'vad-only' | 'float32';

/**
 * Self-contained AudioWorklet registration function serialized into a Blob URL.
 * Keep zero runtime imports or outer-scope references so `toString()` runs in AudioWorkletGlobalScope.
 */
export function registerPcmWorkletProcessor(
  processorName: string,
  targetSampleRate: number | null,
  frameSize: number,
  payloadMode: PcmWorkletPayloadMode,
): void {
  class PcmWorkletProcessor extends AudioWorkletProcessor {
    private readonly buffer = new Float32Array(frameSize);
    private readonly passthrough =
      targetSampleRate === null || sampleRate === targetSampleRate;
    private readonly step =
      targetSampleRate === null ? 1 : sampleRate / targetSampleRate;
    private index = 0;
    private phase = 0;
    private lastSample = 0;

    private emitFrame(): void {
      if (payloadMode === 'float32') {
        this.port.postMessage(this.buffer.slice());
        this.index = 0;
        return;
      }
      let sumSquares = 0;
      for (let i = 0; i < this.buffer.length; i++) {
        const value = this.buffer[i];
        sumSquares += value * value;
      }
      const pcm16 = new Int16Array(this.buffer.length);
      for (let i = 0; i < this.buffer.length; i++) {
        const clamped = Math.max(-1, Math.min(1, this.buffer[i]));
        pcm16[i] = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
      }
      const pcm = new Uint8Array(pcm16.buffer);
      const rms = Math.sqrt(sumSquares / this.buffer.length);
      if (payloadMode === 'pcm-vad') {
        this.port.postMessage({ pcm, float32: this.buffer.slice(), rms });
      } else {
        this.port.postMessage({ pcm, rms });
      }
      this.index = 0;
    }

    override process(inputs: readonly (readonly Float32Array[])[]): boolean {
      const input = inputs[0]?.[0];
      if (!input) return true;
      if (this.passthrough) {
        for (let i = 0; i < input.length; i++) {
          this.buffer[this.index++] = input[i];
          if (this.index === this.buffer.length) {
            this.emitFrame();
          }
        }
        return true;
      }
      for (let n = 0; n < input.length; n++) {
        const current = input[n];
        while (this.phase <= n) {
          const previous = n > 0 ? input[n - 1] : this.lastSample;
          const t = this.phase - (n - 1);
          this.buffer[this.index++] = previous + (current - previous) * t;
          if (this.index === this.buffer.length) {
            this.emitFrame();
          }
          this.phase += this.step;
        }
        this.lastSample = current;
      }
      this.phase -= input.length;
      return true;
    }
  }

  registerProcessor(processorName, PcmWorkletProcessor);
}

/** Builds an executable AudioWorklet module source string from the compiled TypeScript processor. */
export function buildPcmWorkletSource(
  processorName: string,
  targetSampleRate: number | null,
  frameSize: number,
  payloadMode: PcmWorkletPayloadMode,
): string {
  return `(${registerPcmWorkletProcessor.toString()})(${JSON.stringify(processorName)}, ${JSON.stringify(targetSampleRate)}, ${JSON.stringify(frameSize)}, ${JSON.stringify(payloadMode)});`;
}
