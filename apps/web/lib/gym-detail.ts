import { demoGyms, type Gym } from "./demo-data";

export function getGymById(id: string): Gym | undefined {
  return demoGyms.find((gym) => gym.id === id);
}

export function priceText(value: number | null, suffix: string): string {
  if (value === 0) return `Free${suffix}`;
  return value === null ? "Not listed" : `$${value.toFixed(2)}${suffix}`;
}

export function safeExternalUrl(value: string | undefined): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : undefined;
  } catch {
    return undefined;
  }
}

export function priceFreshnessText(gym: Gym): string {
  if (gym.priceSource) return `Official source checked ${gym.priceObservedAt || "recently"}.`;
  if (gym.freshness === "gym_reported") return "Price reported by the gym.";
  if (gym.freshness === "stale") return "Price may be out of date.";
  if (gym.freshness === "verified") return "Price verified recently.";
  return "No price was published in the directory source.";
}
