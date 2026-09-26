import { describe, expect, it } from "vitest";
import { age, formatMs } from "./adjuster";

describe("queue age", () => {
  const now = Date.parse("2026-09-25T12:00:00Z");
  it("formats minutes, hours, and days", () => {
    expect(age("2026-09-25T11:59:50Z", now)).toBe("just now");
    expect(age("2026-09-25T11:48:00Z", now)).toBe("12 min ago");
    expect(age("2026-09-25T09:00:00Z", now)).toBe("3 h ago");
    expect(age("2026-09-22T12:00:00Z", now)).toBe("3 d ago");
  });
});

describe("formatMs", () => {
  it("never shows a missing measurement as zero", () => {
    expect(formatMs(null)).toBe("No data");
    expect(formatMs(840)).toBe("840 ms");
    expect(formatMs(1500)).toBe("1.5 s");
  });
});
