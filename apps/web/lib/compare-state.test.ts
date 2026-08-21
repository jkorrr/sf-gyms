import { describe, expect, it } from "vitest";

import { buildComparisonParams, compactCompareSlots, createCompareSlots, parseComparisonParams, removeCompareSlot, sanitizeCompareIds, setCompareSlot } from "./compare-state";

describe("comparison URL state", () => {
  const validIds = new Set(["a", "b", "c", "d"]);

  it("deduplicates, validates, and limits gym ids", () => {
    expect(sanitizeCompareIds(["a", "bad", "a", "b", "c", "d"], validIds)).toEqual(["a", "b", "c"]);
  });

  it("clamps visits and rejects invalid coordinates", () => {
    const parsed = parseComparisonParams(new URLSearchParams("gyms=a,b&visits=99&months=7&lat=1&lng=1"), validIds);
    expect(parsed.ids).toEqual(["a", "b"]);
    expect(parsed.assumptions.visitsPerWeek).toBe(14);
    expect(parsed.assumptions.months).toBe(12);
    expect(parsed.assumptions.origin).toBeNull();
  });

  it("rounds shared coordinates to three decimals", () => {
    const params = buildComparisonParams(["a", "b"], {
      visitsPerWeek: 3,
      months: 12,
      origin: { latitude: 37.75649, longitude: -122.40149, label: "Home" },
    });
    expect(params.get("lat")).toBe("37.756");
    expect(params.get("lng")).toBe("-122.401");
  });

  it("appends and replaces a specific comparison slot without changing order", () => {
    expect(setCompareSlot(createCompareSlots(["a"]), 1, "b")).toEqual(["a", "b", null]);
    expect(setCompareSlot(createCompareSlots(["a", "b", "c"]), 1, "d")).toEqual(["a", "d", "c"]);
  });

  it("keeps every chooser independent while compacting persisted ids", () => {
    const thirdOnly = setCompareSlot(createCompareSlots(), 2, "c");
    expect(thirdOnly).toEqual([null, null, "c"]);
    expect(compactCompareSlots(thirdOnly)).toEqual(["c"]);
    expect(removeCompareSlot(["a", "b", "c"], 1)).toEqual(["a", null, "c"]);
  });

  it("rejects duplicate selections across slots", () => {
    expect(setCompareSlot(createCompareSlots(["a", "b"]), 1, "a")).toEqual(["a", "b", null]);
  });
});
