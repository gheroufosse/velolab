import { describe, expect, it } from "vitest";
import { formatDistance, formatDuration, formatLoad, formatLocalStart } from "./activityFormat";

// Isolation inventory: null/invalid metrics must be gaps, not zero; real zero
// must survive; minutes/hours must not wrap; provider-local text must not shift
// with browser timezone. Page coverage protects wiring; these pure boundary
// examples protect presentation independently. I/O/concurrency/auth are not
// formatter responsibilities (covered through the page/coordinator instead).
describe("activity display", () => {
  it("distinguishes zero from missing or invalid metrics", () => {
    expect([formatDuration(0), formatDistance(0), formatLoad(0)]).toEqual(["0:00", "0", "0"]);
    for (const value of [null, -1, NaN, Infinity]) {
      expect([formatDuration(value), formatDistance(value), formatLoad(value)]).toEqual(["—", "—", "—"]);
    }
  });

  it("formats seconds as h:mm and metres as kilometres without computing load", () => {
    expect(formatDuration(3599)).toBe("0:59");
    expect(formatDuration(3661)).toBe("1:01");
    expect(formatDuration(90000)).toBe("25:00");
    expect(formatDistance(12345)).toBe("12.3");
    expect(formatLoad(42.5)).toBe("42.5");
  });

  it("keeps the provider-local clock text unchanged", () => {
    expect(formatLocalStart("2026-10-08T23:30:00")).toBe("2026-10-08 23:30:00");
    expect(formatLocalStart(null)).toBe("—");
    expect(formatLocalStart("invalid")).toBe("—");
  });
});
