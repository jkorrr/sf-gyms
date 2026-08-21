import { type Gym, venueTypeLabels } from "./demo-data";

function normalize(value: string): string {
  return value.trim().toLocaleLowerCase().replace(/\s+/g, " ");
}

function matchRank(gym: Gym, query: string): number | null {
  const needle = normalize(query);
  if (!needle) return 3;

  const name = normalize(gym.name);
  if (name.startsWith(needle)) return 0;
  if (name.includes(needle)) return 1;

  const searchableDetails = normalize([
    gym.neighborhood,
    gym.address,
    venueTypeLabels[gym.venueType],
    gym.gymType,
  ].join(" "));
  const tokens = needle.split(" ");
  return tokens.every((token) => searchableDetails.includes(token) || name.includes(token)) ? 2 : null;
}

export function searchGymsForComparison(gyms: Gym[], query: string, excludedIds: Set<string>): Gym[] {
  return gyms
    .map((gym) => ({ gym, rank: excludedIds.has(gym.id) ? null : matchRank(gym, query) }))
    .filter((item): item is { gym: Gym; rank: number } => item.rank !== null)
    .sort((left, right) => left.rank - right.rank
      || left.gym.name.localeCompare(right.gym.name)
      || left.gym.neighborhood.localeCompare(right.gym.neighborhood)
      || left.gym.address.localeCompare(right.gym.address))
    .map(({ gym }) => gym);
}
