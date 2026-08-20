import { describe, expect, it } from "vitest";

import { buildComparisonParams, parseComparisonParams, sanitizeCompareIds } from "./compare-state";

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
});

