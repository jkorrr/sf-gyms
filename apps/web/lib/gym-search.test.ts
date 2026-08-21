import { describe, expect, it } from "vitest";

import { demoGyms, type Gym } from "./demo-data";
import { searchGymsForComparison } from "./gym-search";

const base = demoGyms[0];
const catalog: Gym[] = [
  { ...base, id: "contains", name: "Mission Iron House", neighborhood: "Mission", address: "200 Valencia Street", gymType: "Strength gym", venueType: "traditional_gym" },
  { ...base, id: "prefix", name: "Iron Works", neighborhood: "SOMA", address: "100 Brannan Street", gymType: "Traditional gym", venueType: "traditional_gym" },
  { ...base, id: "address", name: "Bay Athletics", neighborhood: "Potrero Hill", address: "1455 18th Street", gymType: "Open gym", venueType: "traditional_gym" },
  { ...base, id: "boxing", name: "City Strikers", neighborhood: "Dogpatch", address: "300 3rd Street", gymType: "Boxing club", venueType: "martial_arts_boxing" },
];

describe("full catalog comparison search", () => {
  it("does not cap unfiltered results at twelve", () => {
    expect(searchGymsForComparison(demoGyms, "", new Set())).toHaveLength(demoGyms.length);
    expect(demoGyms.length).toBeGreaterThan(12);
  });

  it("ranks name prefixes before name containment", () => {
    expect(searchGymsForComparison(catalog, "iron", new Set()).map((gym) => gym.id)).toEqual(["prefix", "contains"]);
  });

  it("searches neighborhood, address, venue type, and subtype", () => {
    expect(searchGymsForComparison(catalog, "Potrero", new Set()).map((gym) => gym.id)).toEqual(["address"]);
    expect(searchGymsForComparison(catalog, "1455 18th", new Set()).map((gym) => gym.id)).toEqual(["address"]);
    expect(searchGymsForComparison(catalog, "martial arts", new Set()).map((gym) => gym.id)).toEqual(["boxing"]);
    expect(searchGymsForComparison(catalog, "boxing club", new Set()).map((gym) => gym.id)).toEqual(["boxing"]);
  });

  it("excludes gyms already selected in another slot", () => {
    expect(searchGymsForComparison(catalog, "", new Set(["prefix"])).some((gym) => gym.id === "prefix")).toBe(false);
  });
});
