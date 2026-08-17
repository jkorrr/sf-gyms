import type { Gym } from "./demo-data";

export type GeoPoint = {
  latitude: number;
  longitude: number;
  label: string;
};

const EARTH_RADIUS_MILES = 3958.7613;

function toRadians(value: number): number {
  return value * (Math.PI / 180);
}

export function distanceMiles(from: GeoPoint, gym: Pick<Gym, "latitude" | "longitude">): number {
  const latitudeDelta = toRadians(gym.latitude - from.latitude);
  const longitudeDelta = toRadians(gym.longitude - from.longitude);
  const latitudeOne = toRadians(from.latitude);
  const latitudeTwo = toRadians(gym.latitude);
  const haversine = Math.sin(latitudeDelta / 2) ** 2
    + Math.cos(latitudeOne) * Math.cos(latitudeTwo) * Math.sin(longitudeDelta / 2) ** 2;
  return EARTH_RADIUS_MILES * 2 * Math.atan2(Math.sqrt(haversine), Math.sqrt(1 - haversine));
}

export function formatDistanceMiles(distance: number): string {
  if (distance < 0.1) return "Less than 0.1 mi";
  return `${distance.toFixed(distance < 10 ? 1 : 0)} mi away`;
}
