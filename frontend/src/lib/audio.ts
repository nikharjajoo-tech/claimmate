// Audio plumbing between the browser and Gemini Live.
// Mic: device rate (44.1/48 kHz float) -> 16 kHz PCM16. Speaker: 24 kHz PCM16 -> queued playback.

export const INPUT_RATE = 16_000;
export const OUTPUT_RATE = 24_000;
const CHUNK_SAMPLES = INPUT_RATE / 10; // send ~100 ms at a time

/** Downsample by averaging each output sample's source window, then clamp to PCM16. */
export function resampleToPcm16(input: Float32Array, inputRate: number, outputRate = INPUT_RATE): Int16Array {
  const ratio = inputRate / outputRate;
  const length = Math.floor(input.length / ratio);
  const out = new Int16Array(length);
  for (let i = 0; i < length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.max(start + 1, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end && j < input.length; j++) sum += input[j];
    const sample = Math.max(-1, Math.min(1, sum / (end - start)));
    out[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return out;
}

export function pcm16ToFloat32(bytes: Uint8Array): Float32Array {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const out = new Float32Array(Math.floor(bytes.byteLength / 2));
  for (let i = 0; i < out.length; i++) out[i] = view.getInt16(i * 2, true) / 0x8000;
  return out;
}

export function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

export function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64);
  const out = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) out[i] = binary.charCodeAt(i);
  return out;
}

/** Plays agent audio chunks back-to-back and can cut them all off instantly on barge-in. */
export class AudioPlayer {
  private ctx: AudioContext | null = null;
  private nextStart = 0;
  private sources = new Set<AudioBufferSourceNode>();
  private readonly makeContext: () => AudioContext;

  constructor(makeContext: () => AudioContext = () => new AudioContext()) {
    this.makeContext = makeContext;
  }

  /** Must be called from a user gesture (e.g. the Talk click) so browsers allow sound. */
  async unlock(): Promise<void> {
    this.ctx ??= this.makeContext();
    if (this.ctx.state === "suspended") await this.ctx.resume();
  }

  get speaking(): boolean {
    return this.sources.size > 0;
  }

  play(base64: string): void {
    this.ctx ??= this.makeContext();
    const samples = pcm16ToFloat32(base64ToBytes(base64));
    if (!samples.length) return;
    const buffer = this.ctx.createBuffer(1, samples.length, OUTPUT_RATE);
    buffer.getChannelData(0).set(samples);
    const source = this.ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(this.ctx.destination);
    const startAt = Math.max(this.ctx.currentTime, this.nextStart);
    source.start(startAt);
    this.nextStart = startAt + buffer.duration;
    this.sources.add(source);
    source.onended = () => this.sources.delete(source);
  }

  stop(): void {
    for (const source of this.sources) {
      try {
        source.stop();
      } catch {
        // already stopped
      }
    }
    this.sources.clear();
    this.nextStart = 0;
  }
}

/** Streams the microphone as base64 PCM16 @ 16 kHz, ~100 ms per chunk. */
export class MicCapture {
  private stream: MediaStream | null = null;
  private ctx: AudioContext | null = null;
  private node: AudioWorkletNode | null = null;
  private buffered: Int16Array[] = [];
  private bufferedSamples = 0;

  async start(onChunk: (base64: string) => void): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    });
    this.ctx = new AudioContext();
    await this.ctx.audioWorklet.addModule("/pcm-capture.js"); // static file, allowed by script-src 'self'
    const source = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "pcm-capture");
    const rate = this.ctx.sampleRate;
    this.node.port.onmessage = (event: MessageEvent<Float32Array>) => {
      const pcm = resampleToPcm16(event.data, rate);
      this.buffered.push(pcm);
      this.bufferedSamples += pcm.length;
      if (this.bufferedSamples >= CHUNK_SAMPLES) {
        const merged = new Int16Array(this.bufferedSamples);
        let offset = 0;
        for (const part of this.buffered) {
          merged.set(part, offset);
          offset += part.length;
        }
        this.buffered = [];
        this.bufferedSamples = 0;
        onChunk(bytesToBase64(new Uint8Array(merged.buffer)));
      }
    };
    source.connect(this.node);
  }

  stop(): void {
    this.node?.port.close();
    this.node?.disconnect();
    this.stream?.getTracks().forEach((track) => track.stop());
    void this.ctx?.close();
    this.node = null;
    this.stream = null;
    this.ctx = null;
    this.buffered = [];
    this.bufferedSamples = 0;
  }
}
