import type { ComparisonAssumptions } from "./gym-value";
import { DEFAULT_COMPARISON_ASSUMPTIONS } from "./gym-value";

export const COMPARE_IDS_KEY = "sf-gyms:compare";
export const COMPARE_ASSUMPTIONS_KEY = "sf-gyms:compare-assumptions:v1";
export const ALLOWED_MONTHS = [1, 3, 6, 12, 24] as const;

type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem">;

export function sanitizeCompareIds(ids: string[], validIds: Set<string>): string[] {
  return Array.from(new Set(ids.filter((id) => validIds.has(id)))).slice(0, 3);
}

export function readCompareIds(storage: StorageLike, validIds: Set<string>): string[] {
  try {
    const value = JSON.parse(storage.getItem(COMPARE_IDS_KEY) ?? "[]") as unknown;
    return Array.isArray(value) && value.every((item) => typeof item === "string")
      ? sanitizeCompareIds(value, validIds)
      : [];
  } catch {
    return [];
  }
}

export function writeCompareIds(storage: StorageLike, ids: string[]) {
  if (ids.length === 0) storage.removeItem(COMPARE_IDS_KEY);
  else storage.setItem(COMPARE_IDS_KEY, JSON.stringify(ids.slice(0, 3)));
}

function sanitizeVisits(value: unknown): number {
  const parsed = Number(value);
  return Number.isInteger(parsed) ? Math.min(14, Math.max(1, parsed)) : DEFAULT_COMPARISON_ASSUMPTIONS.visitsPerWeek;
}

function sanitizeMonths(value: unknown): ComparisonAssumptions["months"] {
  const parsed = Number(value);
  return ALLOWED_MONTHS.includes(parsed as ComparisonAssumptions["months"])
    ? parsed as ComparisonAssumptions["months"]
    : DEFAULT_COMPARISON_ASSUMPTIONS.months;
}

function sanitizeOrigin(latitude: unknown, longitude: unknown) {
  const lat = Number(latitude);
  const lng = Number(longitude);
  if (!Number.isFinite(lat) || !Number.isFinite(lng)) return null;
  if (lat < 37.6 || lat > 37.9 || lng < -122.6 || lng > -122.2) return null;
  return { latitude: lat, longitude: lng, label: "Selected area" };
}

export function readStoredAssumptions(storage: StorageLike): ComparisonAssumptions {
  try {
    const value = JSON.parse(storage.getItem(COMPARE_ASSUMPTIONS_KEY) ?? "null") as Record<string, unknown> | null;
    if (!value || value.v !== 1) return DEFAULT_COMPARISON_ASSUMPTIONS;
    return {
      visitsPerWeek: sanitizeVisits(value.visitsPerWeek),
      months: sanitizeMonths(value.months),
      origin: sanitizeOrigin(value.latitude, value.longitude),
    };
  } catch {
    return DEFAULT_COMPARISON_ASSUMPTIONS;
  }
}

export function writeStoredAssumptions(storage: StorageLike, assumptions: ComparisonAssumptions) {
  storage.setItem(COMPARE_ASSUMPTIONS_KEY, JSON.stringify({
    v: 1,
    visitsPerWeek: assumptions.visitsPerWeek,
    months: assumptions.months,
    latitude: assumptions.origin?.latitude,
    longitude: assumptions.origin?.longitude,
  }));
}

export function parseComparisonParams(params: URLSearchParams, validIds: Set<string>) {
  const ids = sanitizeCompareIds((params.get("gyms") ?? "").split(",").filter(Boolean), validIds);
  const hasAssumptions = params.has("visits") || params.has("months") || params.has("lat") || params.has("lng");
  const assumptions: ComparisonAssumptions = {
    visitsPerWeek: sanitizeVisits(params.get("visits")),
    months: sanitizeMonths(params.get("months")),
    origin: sanitizeOrigin(params.get("lat"), params.get("lng")),
  };
  return { ids, assumptions, hasAssumptions };
}

export function buildComparisonParams(ids: string[], assumptions: ComparisonAssumptions): URLSearchParams {
  const params = new URLSearchParams();
  if (ids.length > 0) params.set("gyms", ids.slice(0, 3).join(","));
  params.set("visits", String(assumptions.visitsPerWeek));
  params.set("months", String(assumptions.months));
  if (assumptions.origin) {
    params.set("lat", assumptions.origin.latitude.toFixed(3));
    params.set("lng", assumptions.origin.longitude.toFixed(3));
  }
  return params;
}

