import { describe, expect, it } from "vitest";
import { batchPhotos, fitWithin, stripDataUrl } from "./camera";

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

  it("batches photos by count and by size, keeping order", () => {
    const photo = (name: string, size: number) => ({ name, data: "x".repeat(size) });
    const names = (batches: { name: string }[][]) => batches.map((b) => b.map((p) => p.name).join(""));
    const five = ["a", "b", "c", "d", "e"].map((n) => photo(n, 1));
    expect(names(batchPhotos(five, 2, 100))).toEqual(["ab", "cd", "e"]);
    expect(names(batchPhotos([photo("a", 6), photo("b", 5), photo("c", 4)], 10, 10))).toEqual(["a", "bc"]);
    expect(names(batchPhotos([photo("a", 20), photo("b", 1)], 10, 10))).toEqual(["a", "b"]); // oversized goes alone
    expect(batchPhotos([])).toEqual([]);
  });
});
