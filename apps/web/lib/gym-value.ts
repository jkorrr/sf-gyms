import type { Gym } from "./demo-data";
import { distanceMiles, type GeoPoint } from "./geo";

export type SortOrder = "best_match" | "first_year_cost" | "monthly" | "day_pass" | "cost_per_visit" | "distance" | "name";

export type ComparisonAssumptions = {
  visitsPerWeek: number;
  months: 1 | 2 | 3 | 6 | 12 | 24;
  origin: GeoPoint | null;
};

export type PurchaseRecommendation = "membership" | "day_pass" | "only_membership" | "only_day_pass" | "unavailable";

export type GymCostEstimate = {
  monthlyRate: number | null;
  usesUnlimitedRate: boolean;
  estimatedVisits: number;
  membershipTotal: number | null;
  dayPassTotal: number | null;
  effectiveMonthly: number | null;
  membershipCostPerVisit: number | null;
  dayPassCostPerVisit: number | null;
  breakEvenVisitsPerMonth: number | null;
  recommendation: PurchaseRecommendation;
};

export type RankedGym = {
  gym: Gym;
  rank: number;
  score: number;
  distance: number | null;
  estimate: GymCostEstimate;
  why: string;
};

export const DEFAULT_COMPARISON_ASSUMPTIONS: ComparisonAssumptions = {
  visitsPerWeek: 3,
  months: 12,
  origin: null,
};

const money = (value: number) => `$${Math.round(value).toLocaleString("en-US")}`;

export function estimateGymCost(gym: Gym, assumptions: ComparisonAssumptions): GymCostEstimate {
  const monthlyRate = gym.monthlyUnlimitedPrice ?? gym.monthlyPrice;
  const usesUnlimitedRate = gym.monthlyUnlimitedPrice !== undefined && gym.monthlyUnlimitedPrice !== null;
  const estimatedVisits = assumptions.visitsPerWeek * (52 / 12) * assumptions.months;
  const enrollmentFee = gym.enrollmentFee ?? 0;
  const initiationFee = gym.initiationFee ?? 0;
  const annualFee = gym.annualFee ?? 0;
  const annualFeeOccurrences = Math.ceil(assumptions.months / 12);
  const membershipTotal = monthlyRate === null
    ? null
    : monthlyRate * assumptions.months + enrollmentFee + initiationFee + annualFee * annualFeeOccurrences;
  const dayPassTotal = gym.dayPassPrice === null ? null : gym.dayPassPrice * estimatedVisits;
  const effectiveMonthly = membershipTotal === null ? null : membershipTotal / assumptions.months;
  const membershipCostPerVisit = membershipTotal === null || estimatedVisits === 0 ? null : membershipTotal / estimatedVisits;
  const dayPassCostPerVisit = gym.dayPassPrice;
  const annualizedMonthlyFees = annualFee / 12 + (enrollmentFee + initiationFee) / assumptions.months;
  const breakEvenVisitsPerMonth = monthlyRate === null || gym.dayPassPrice === null || gym.dayPassPrice <= 0
    ? null
    : Math.ceil((monthlyRate + annualizedMonthlyFees) / gym.dayPassPrice);

  let recommendation: PurchaseRecommendation = "unavailable";
  if (membershipTotal !== null && dayPassTotal !== null) recommendation = membershipTotal <= dayPassTotal ? "membership" : "day_pass";
  else if (membershipTotal !== null) recommendation = "only_membership";
  else if (dayPassTotal !== null) recommendation = "only_day_pass";

  return {
    monthlyRate,
    usesUnlimitedRate,
    estimatedVisits,
    membershipTotal,
    dayPassTotal,
    effectiveMonthly,
    membershipCostPerVisit,
    dayPassCostPerVisit,
    breakEvenVisitsPerMonth,
    recommendation,
  };
}

function textRelevance(gym: Gym, query: string): number {
  const needle = query.trim().toLowerCase();
  if (!needle) return 0;
  const name = gym.name.toLowerCase();
  if (name === needle) return 40;
  if (name.startsWith(needle)) return 36;
  if (name.includes(needle)) return 32;
  if (`${gym.neighborhood} ${gym.gymType}`.toLowerCase().includes(needle)) return 24;
  if (gym.amenities.some((amenity) => amenity.toLowerCase().includes(needle))) return 16;
  return 0;
}

function priceTrust(gym: Gym): number {
  if (gym.priceSource && gym.freshness === "verified") return 20;
  if (gym.freshness === "verified") return 17;
  if (gym.freshness === "gym_reported") return 14;
  if (gym.freshness === "stale") return 6;
  return 0;
}

function listingCompleteness(gym: Gym): number {
  let score = 0;
  if (gym.monthlyPrice !== null || gym.dayPassPrice !== null) score += 6;
  if (gym.hours && gym.hours !== "Hours not listed" && gym.hours !== "Hours vary") score += 3;
  if (gym.websiteUrl || gym.sourceUrl) score += 3;
  if (gym.amenities.length > 0) score += 3;
  return score;
}

function bestMatchScore(gym: Gym, query: string, distance: number | null): number {
  const distanceScore = distance === null ? 0 : 25 * Math.max(0, 1 - distance / 10);
  return textRelevance(gym, query) + distanceScore + priceTrust(gym) + listingCompleteness(gym);
}

function compareNullable(left: number | null, right: number | null): number {
  if (left === null && right === null) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  return left - right;
}

function whyRanked(row: Omit<RankedGym, "rank" | "why">, sortOrder: SortOrder): string {
  const { gym, distance, estimate } = row;
  if (sortOrder === "first_year_cost" && estimate.membershipTotal !== null) return `${money(estimate.membershipTotal)} estimated first year`;
  if (sortOrder === "monthly" && estimate.monthlyRate !== null) return `${money(estimate.monthlyRate)}/month`;
  if (sortOrder === "day_pass" && gym.dayPassPrice !== null) return `${money(gym.dayPassPrice)} day pass`;
  if (sortOrder === "cost_per_visit" && estimate.membershipCostPerVisit !== null) return `${money(estimate.membershipCostPerVisit)} estimated per visit`;
  if (sortOrder === "distance" && distance !== null) return `${distance < 10 ? distance.toFixed(1) : Math.round(distance)} miles away`;
  if (sortOrder === "name") return "Sorted by gym name";
  const reasons: string[] = [];
  if (distance !== null) reasons.push(`${distance < 10 ? distance.toFixed(1) : Math.round(distance)} mi away`);
  if (gym.priceSource) reasons.push("official price source");
  else if (gym.monthlyPrice !== null || gym.dayPassPrice !== null) reasons.push("price available");
  if (gym.amenities.length > 0) reasons.push(`${gym.amenities.length} amenities listed`);
  return reasons.slice(0, 2).join(" · ") || "Complete local listing";
}

export function rankGyms(
  gyms: Gym[],
  options: { sortOrder: SortOrder; query: string; origin: GeoPoint | null; assumptions: ComparisonAssumptions },
): RankedGym[] {
  const rows = gyms.map((gym) => {
    const distance = options.origin ? distanceMiles(options.origin, gym) : null;
    return {
      gym,
      distance,
      estimate: estimateGymCost(gym, options.assumptions),
      score: bestMatchScore(gym, options.query, distance),
    };
  });

  rows.sort((left, right) => {
    let compared = 0;
    if (options.sortOrder === "best_match") compared = right.score - left.score;
    else if (options.sortOrder === "first_year_cost") compared = compareNullable(left.estimate.membershipTotal, right.estimate.membershipTotal);
    else if (options.sortOrder === "monthly") compared = compareNullable(left.estimate.monthlyRate, right.estimate.monthlyRate);
    else if (options.sortOrder === "day_pass") compared = compareNullable(left.gym.dayPassPrice, right.gym.dayPassPrice);
    else if (options.sortOrder === "cost_per_visit") compared = compareNullable(left.estimate.membershipCostPerVisit, right.estimate.membershipCostPerVisit);
    else if (options.sortOrder === "distance") compared = compareNullable(left.distance, right.distance);
    else if (options.sortOrder === "name") compared = left.gym.name.localeCompare(right.gym.name);

    if (compared !== 0) return compared;
    if (options.sortOrder === "best_match") {
      const costTie = compareNullable(left.estimate.membershipTotal, right.estimate.membershipTotal);
      if (costTie !== 0) return costTie;
    }
    return left.gym.name.localeCompare(right.gym.name);
  });

  return rows.map((row, index) => ({ ...row, rank: index + 1, why: whyRanked(row, options.sortOrder) }));
}
