import { describe, expect, it } from "vitest";

import { demoGyms, type Gym } from "./demo-data";
import { DEFAULT_COMPARISON_ASSUMPTIONS, estimateGymCost, rankGyms } from "./gym-value";

function gym(overrides: Partial<Gym> = {}): Gym {
  return {
    id: "gym-a",
    name: "Gym A",
    neighborhood: "Mission",
    address: "1 Mission St",
    gymType: "Fitness centre",
    venueType: "traditional_gym",
    latitude: 37.76,
    longitude: -122.42,
    monthlyPrice: 100,
    annualFee: 60,
    dayPassPrice: 25,
    freshness: "verified",
    isOpen247: false,
    amenities: ["Squat racks"],
    description: "Test gym",
    hours: "6am–10pm",
    websiteUrl: "https://example.com",
    sourceName: "Official",
    sourceId: "a",
    sourceUrl: "https://example.com",
    importedAt: "2026-08-01",
    priceSource: "Official",
    ...overrides,
  };
}

describe("estimateGymCost", () => {
  it("includes recurring and mandatory first-year fees", () => {
    const result = estimateGymCost(gym({ enrollmentFee: 25, initiationFee: 15 }), DEFAULT_COMPARISON_ASSUMPTIONS);
    expect(result.membershipTotal).toBe(1300);
    expect(result.effectiveMonthly).toBeCloseTo(108.333, 2);
    expect(result.estimatedVisits).toBe(156);
    expect(result.membershipCostPerVisit).toBeCloseTo(8.333, 2);
    expect(result.dayPassTotal).toBe(3900);
    expect(result.recommendation).toBe("membership");
  });

  it("uses unlimited pricing when it is available", () => {
    const result = estimateGymCost(gym({ monthlyPrice: 80, monthlyUnlimitedPrice: 140 }), DEFAULT_COMPARISON_ASSUMPTIONS);
    expect(result.monthlyRate).toBe(140);
    expect(result.usesUnlimitedRate).toBe(true);
  });

  it("keeps free prices valid and missing prices unavailable", () => {
    expect(estimateGymCost(gym({ monthlyPrice: 0, annualFee: 0 }), DEFAULT_COMPARISON_ASSUMPTIONS).membershipTotal).toBe(0);
    expect(estimateGymCost(gym({ monthlyPrice: null, annualFee: null }), DEFAULT_COMPARISON_ASSUMPTIONS).membershipTotal).toBeNull();
  });

  it("charges annual fees once per started year", () => {
    const result = estimateGymCost(gym(), { ...DEFAULT_COMPARISON_ASSUMPTIONS, months: 24 });
    expect(result.membershipTotal).toBe(2520);
  });

  it("keeps disclosed catalog fees in structured comparison totals", () => {
    const planetFitness = demoGyms.find((item) => item.id === "osm-node-1206893699");
    expect(planetFitness?.annualFee).toBe(49);
    expect(estimateGymCost(planetFitness as Gym, DEFAULT_COMPARISON_ASSUMPTIONS).membershipTotal).toBe(229);
  });
});

describe("rankGyms", () => {
  it("puts unknown monthly prices after known prices", () => {
    const ranked = rankGyms(
      [gym({ id: "unknown", name: "Unknown", monthlyPrice: null }), gym({ id: "known", name: "Known", monthlyPrice: 50 })],
      { sortOrder: "monthly", query: "", origin: null, assumptions: DEFAULT_COMPARISON_ASSUMPTIONS },
    );
    expect(ranked.map((row) => row.gym.id)).toEqual(["known", "unknown"]);
  });

  it("uses stable alphabetical tie breaking", () => {
    const ranked = rankGyms(
      [gym({ id: "b", name: "Beta" }), gym({ id: "a", name: "Alpha" })],
      { sortOrder: "monthly", query: "", origin: null, assumptions: DEFAULT_COMPARISON_ASSUMPTIONS },
    );
    expect(ranked.map((row) => row.gym.name)).toEqual(["Alpha", "Beta"]);
  });
});
