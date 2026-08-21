import type { FeatureCollection, Point } from "geojson";

import type { Gym } from "./demo-data";

export type GymPointProperties = {
  id: string;
};

export function buildGymFeatureCollection(gyms: Gym[]): FeatureCollection<Point, GymPointProperties> {
  return {
    type: "FeatureCollection",
    features: gyms.map((gym) => ({
      type: "Feature",
      geometry: {
        type: "Point",
        coordinates: [gym.longitude, gym.latitude],
      },
      properties: { id: gym.id },
    })),
  };
}
