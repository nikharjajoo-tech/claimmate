import { describe, expect, it } from "vitest";
import { AudioPlayer, base64ToBytes, bytesToBase64, pcm16ToFloat32, resampleToPcm16 } from "./audio";

describe("resampleToPcm16", () => {
  it("downsamples 48 kHz to 16 kHz (3:1)", () => {
    const out = resampleToPcm16(new Float32Array(4800).fill(0.5), 48_000);
    expect(out.length).toBe(1600);
    expect(out[0]).toBe(Math.floor(0.5 * 0x7fff));
  });

  it("handles non-integer ratios like 44.1 kHz", () => {
    expect(resampleToPcm16(new Float32Array(4410), 44_100).length).toBe(1600);
  });

  it("clamps out-of-range samples to full scale", () => {
    const out = resampleToPcm16(Float32Array.from([2, 2, 2, -2, -2, -2]), 48_000);
    expect(Array.from(out)).toEqual([0x7fff, -0x8000]);
  });
});

describe("PCM and base64 round trip", () => {
  it("survives encode/decode", () => {
    const pcm = Int16Array.from([0, 1000, -1000, 32767, -32768]);
    const bytes = base64ToBytes(bytesToBase64(new Uint8Array(pcm.buffer)));
    const floats = pcm16ToFloat32(bytes);
    expect(floats[1]).toBeCloseTo(1000 / 0x8000);
    expect(floats[4]).toBe(-1);
  });

  it("encodes large buffers without overflowing the call stack", () => {
    const big = new Uint8Array(200_000).fill(7);
    expect(base64ToBytes(bytesToBase64(big))).toEqual(big);
  });
});

class FakeSource {
  buffer: unknown = null;
  startAt = -1;
  stopped = false;
  onended: (() => void) | null = null;
  connect() {}
  start(at: number) {
    this.startAt = at;
  }
  stop() {
    this.stopped = true;
  }
}

class FakeContext {
  currentTime = 5;
  state = "running";
  destination = {};
  sources: FakeSource[] = [];
  createBuffer(_channels: number, length: number, rate: number) {
    const data = new Float32Array(length);
    return { duration: length / rate, getChannelData: () => data };
  }
  createBufferSource() {
    const source = new FakeSource();
    this.sources.push(source);
    return source;
  }
  async resume() {}
}

const oneSecond = bytesToBase64(new Uint8Array(24_000 * 2));

describe("AudioPlayer", () => {
  it("schedules chunks back to back", () => {
    const ctx = new FakeContext();
    const player = new AudioPlayer(() => ctx as unknown as AudioContext);
    player.play(oneSecond);
    player.play(oneSecond);
    expect(ctx.sources.map((s) => s.startAt)).toEqual([5, 6]);
    expect(player.speaking).toBe(true);
  });

  it("stops everything on barge-in and restarts from now", () => {
    const ctx = new FakeContext();
    const player = new AudioPlayer(() => ctx as unknown as AudioContext);
    player.play(oneSecond);
    player.play(oneSecond);
    player.stop();
    expect(ctx.sources.every((s) => s.stopped)).toBe(true);
    expect(player.speaking).toBe(false);
    ctx.currentTime = 5.5;
    player.play(oneSecond);
    expect(ctx.sources[2].startAt).toBe(5.5);
  });
});
