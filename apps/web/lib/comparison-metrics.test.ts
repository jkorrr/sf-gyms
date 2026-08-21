import { describe, expect, it } from "vitest";

import { buildComparisonMetricGroups } from "./comparison-metrics";
import { demoGyms, type Gym } from "./demo-data";
import { estimateGymCost, type ComparisonAssumptions } from "./gym-value";

const assumptions: ComparisonAssumptions = { visitsPerWeek: 3, months: 12, origin: null };
const gyms: Gym[] = [
  { ...demoGyms[0], id: "lower", name: "Lower Cost", monthlyPrice: 50, monthlyUnlimitedPrice: null, annualFee: 25, dayPassPrice: 20, priceNote: "Short note" },
  { ...demoGyms[1], id: "higher", name: "Higher Cost", monthlyPrice: 80, monthlyUnlimitedPrice: null, annualFee: 50, dayPassPrice: 30, priceNote: "A long source note that must remain available in both comparison layouts." },
];

describe("shared comparison metric model", () => {
  it("marks the lowest known comparable value and preserves long notes", () => {
    const groups = buildComparisonMetricGroups({
      gyms,
      estimates: gyms.map((gym) => estimateGymCost(gym, assumptions)),
      distances: [null, null],
      months: assumptions.months,
    });
    const metrics = groups.flatMap((group) => group.metrics);
    const monthly = metrics.find((metric) => metric.key === "membership-rate");
    const notes = metrics.find((metric) => metric.key === "price-notes");

    expect(monthly?.values.map((value) => value.best)).toEqual([true, false]);
    expect(notes?.values[1].text).toContain("must remain available");
    expect(metrics.every((metric) => metric.values.length === gyms.length)).toBe(true);
  });

  it("never highlights a missing price as best", () => {
    const oneMissing = [{ ...gyms[0], monthlyPrice: null }, gyms[1]];
    const groups = buildComparisonMetricGroups({
      gyms: oneMissing,
      estimates: oneMissing.map((gym) => estimateGymCost(gym, assumptions)),
      distances: [null, null],
      months: 24,
    });
    const monthly = groups.flatMap((group) => group.metrics).find((metric) => metric.key === "membership-rate");
    expect(monthly?.values[0].text).toBe("Not listed");
    expect(monthly?.values[0].best).toBe(false);
  });

  it("does not present unknown fees or empty source fields as free or blank", () => {
    const missing = [{ ...gyms[0], enrollmentFee: undefined, initiationFee: undefined, priceObservedAt: "", priceNote: "" }];
    const groups = buildComparisonMetricGroups({
      gyms: missing,
      estimates: missing.map((gym) => estimateGymCost(gym, assumptions)),
      distances: [null],
      months: 12,
    });
    const metrics = groups.flatMap((group) => group.metrics);

    expect(metrics.find((metric) => metric.key === "joining-fee")?.values[0].text).toBe("Not listed");
    expect(metrics.find((metric) => metric.key === "observed")?.values[0].text).toBe("Not listed");
    expect(metrics.find((metric) => metric.key === "price-notes")?.values[0].text).toBe("No additional notes");
  });
});
