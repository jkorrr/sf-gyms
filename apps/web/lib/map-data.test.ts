import { describe, expect, it } from "vitest";

import { demoGyms } from "./demo-data";
import { buildGymFeatureCollection } from "./map-data";

describe("gym map GeoJSON", () => {
  it("represents every filtered gym with only its id and coordinates", () => {
    const gyms = demoGyms.slice(0, 37);
    const collection = buildGymFeatureCollection(gyms);

    expect(collection.features).toHaveLength(gyms.length);
    collection.features.forEach((feature, index) => {
      expect(feature.properties).toEqual({ id: gyms[index].id });
      expect(feature.geometry.coordinates).toEqual([gyms[index].longitude, gyms[index].latitude]);
      expect(feature.properties).not.toHaveProperty("rank");
      expect(feature.properties).not.toHaveProperty("name");
      expect(feature.properties).not.toHaveProperty("description");
    });
  });
});
