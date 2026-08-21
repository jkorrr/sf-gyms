import { demoGyms, type Gym } from "./demo-data";

export function getGymById(id: string): Gym | undefined {
  return demoGyms.find((gym) => gym.id === id);
}

export function priceText(value: number | null, suffix: string): string {
  if (value === 0) return `Free${suffix}`;
  return value === null ? "Not listed" : `$${value.toFixed(2)}${suffix}`;
}

export function monthlyCostText(gym: Gym): string {
  if (gym.monthlyPrice !== null) return priceText(gym.monthlyPrice, "/mo");
  if (gym.operatorConfirmedMonthly?.freshness === "current") return `$${gym.operatorConfirmedMonthly.normalizedMonthly.toFixed(0)}/mo`;
  if (gym.reportedMonthly) return `~$${gym.reportedMonthly.point.toFixed(0)}/mo`;
  if (gym.estimatedMonthly) return `~$${gym.estimatedMonthly.point.toFixed(0)}/mo`;
  const context = gym.costContext?.[0];
  if (context) return context.low === context.high ? `From $${context.low.toFixed(0)}` : `$${context.low.toFixed(0)}–$${context.high.toFixed(0)}`;
  if (gym.pricingStatus === "free") return "Free/public";
  if (gym.pricingStatus === "pay-per-visit") return "Pay per visit";
  if (gym.pricingStatus === "not-applicable") return "Not applicable";
  return "Needs confirmation";
}

export function pricingStatusText(gym: Gym): string {
  if (gym.costContext?.length && !gym.monthlyPrice && !gym.operatorConfirmedMonthly && !gym.reportedMonthly && !gym.estimatedMonthly) return "Official range";
  const labels: Record<string, string> = {
    verified: "Official price",
    "operator-confirmed": "Operator confirmed",
    reported: "Recently reported",
    estimated: "Estimated",
    free: "Free/public",
    "pay-per-visit": "Pay per visit",
    "not-applicable": "Not applicable",
    gated: "Price gated",
    unresolved: "Needs confirmation",
  };
  return labels[gym.pricingStatus ?? (gym.monthlyPrice !== null ? "verified" : "unresolved")];
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
  if (gym.operatorConfirmedMonthly) return `Operator confirmed ${gym.operatorConfirmedMonthly.confirmedAt}; the amount is not publicly reproducible.`;
  if (gym.reportedMonthly) return `Recent public reports checked through ${gym.reportedMonthly.newestPublishedAt}.`;
  if (gym.freshness === "gym_reported") return "Price reported by the gym.";
  if (gym.freshness === "stale") return "Price may be out of date.";
  if (gym.freshness === "verified") return "Price verified recently.";
  return "No price was published in the directory source.";
}
