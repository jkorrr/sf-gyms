import { describe, expect, it } from "vitest";

import type { Gym } from "./demo-data";
import {
  experienceContext,
  experienceSignalLabels,
  reviewLocationId,
  type ExperienceReport,
} from "./experience-reports";

function report(overrides: Partial<ExperienceReport> = {}): ExperienceReport {
  return {
    id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    gym_location_id: "11111111-1111-4111-8111-111111111111",
    visit_date: "2026-08-12",
    time_bucket: "evening",
    relationship: "day_pass",
    equipment_availability: "short_wait",
    crowding: "busy",
    cleanliness: "clean",
    published_at: "2026-08-13T18:00:00Z",
    ...overrides,
  };
}

describe("experience report presentation", () => {
  it("turns structured observations into plain-language labels", () => {
    expect(experienceSignalLabels(report())).toEqual([
      "Short equipment wait",
      "Busy",
      "Clean",
    ]);
  });

  it("keeps visit context explicit and time-stamped", () => {
    expect(experienceContext(report())).toBe("Day-pass visit · visited Aug 12, 2026 · evening");
  });
});

describe("reviewLocationId", () => {
  it("does not treat an OSM fixture key as a production database id", () => {
    expect(reviewLocationId({ id: "osm-node-123" } as Gym)).toBeUndefined();
  });

  it("prefers an explicitly mapped database location id", () => {
    const databaseId = "11111111-1111-4111-8111-111111111111";
    expect(reviewLocationId({ id: "osm-node-123", databaseId } as Gym)).toBe(databaseId);
  });
});
