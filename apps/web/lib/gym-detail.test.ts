import { describe, expect, it } from "vitest";

import type { CostContext, Gym } from "./demo-data";
import { costContextStatusText, costContextText, monthlyCostText, pricingStatusText } from "./gym-detail";

function context(overrides: Partial<CostContext> = {}): CostContext {
  return {
    id: "context-1",
    kind: "package-price",
    productType: "class-pack",
    label: "Three Class Pass",
    low: 40,
    high: 40,
    currency: "USD",
    cadence: "3-class pass",
    evidenceTier: "official-public",
    sourceUrl: "https://example.com/pricing",
    observedAt: "2026-08-21",
    selectable: false,
    ...overrides,
  };
}

describe("official cost context", () => {
  it("labels a fixed class package without implying it is a starting price", () => {
    const value = context();
    expect(costContextText(value)).toBe("$40 / 3-class pass");
    expect(costContextStatusText(value)).toBe("Official cost");
  });

  it("makes an official price conflict explicit", () => {
    const value = context({ kind: "conflicting-price", label: "Ongoing membership", low: 220, high: 220, cadence: "4 weeks", conflictFlags: ["duplicate-contradictory-terms"] });
    expect(costContextText(value)).toBe("Conflicting $220 / 4 weeks");
    expect(costContextStatusText(value)).toBe("Official conflict");
  });

  it("distinguishes starting prices and ranges", () => {
    expect(costContextText(context({ kind: "starting-price", low: 200, high: 200, cadence: "month" }))).toBe("From $200 / month");
    expect(costContextText(context({ kind: "range", low: 150, high: 250, cadence: "session" }))).toBe("$150–$250 / session");
  });

  it("uses context semantics when exact and estimated prices are absent", () => {
    const value = context({ kind: "conflicting-price", low: 220, high: 220, cadence: "4 weeks", conflictFlags: ["terms-conflict"] });
    const gym = { monthlyPrice: null, costContext: [value], pricingStatus: "unresolved" } as Gym;
    expect(monthlyCostText(gym)).toBe("Conflicting $220 / 4 weeks");
    expect(pricingStatusText(gym)).toBe("Official conflict");
  });
});
