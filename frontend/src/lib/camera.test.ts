import { describe, expect, it } from "vitest";
import { fitWithin, stripDataUrl } from "./camera";

describe("camera helpers", () => {
  it("strips the data URL prefix", () => {
    expect(stripDataUrl("data:image/jpeg;base64,/9j/4AAQ")).toBe("/9j/4AAQ");
    expect(stripDataUrl("/9j/raw")).toBe("/9j/raw");
  });

  it("scales down to fit and never upscales", () => {
    expect(fitWithin(1920, 1080, 640)).toEqual({ width: 640, height: 360 });
    expect(fitWithin(1080, 1920, 1280)).toEqual({ width: 720, height: 1280 });
    expect(fitWithin(320, 240, 640)).toEqual({ width: 320, height: 240 });
  });
});
