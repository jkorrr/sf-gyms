import { demoGyms, type CostContext, type Gym } from "./demo-data";

export function getGymById(id: string): Gym | undefined {
  return demoGyms.find((gym) => gym.id === id);
}

export function priceText(value: number | null, suffix: string): string {
  if (value === 0) return `Free${suffix}`;
  return value === null ? "Not listed" : `$${value.toFixed(2)}${suffix}`;
}

export function costContextText(context: CostContext, decimals = 0): string {
  const amount = context.low === context.high
    ? `$${context.low.toFixed(decimals)}`
    : `$${context.low.toFixed(decimals)}–$${context.high.toFixed(decimals)}`;
  const cadence = context.cadence && context.cadence !== "unknown" ? ` / ${context.cadence}` : "";
  if (context.kind === "starting-price") return `From ${amount}${cadence}`;
  if (context.kind === "conflicting-price") return `Conflicting ${amount}${cadence}`;
  return `${amount}${cadence}`;
}

export function costContextStatusText(context: CostContext): string {
  if (context.kind === "conflicting-price" || context.conflictFlags?.length) return "Official conflict";
  if (context.kind === "range" || context.kind === "starting-price") return "Official range";
  return "Official cost";
}

export function monthlyCostText(gym: Gym): string {
  if (gym.monthlyPrice !== null) return priceText(gym.monthlyPrice, "/mo");
  if (gym.operatorConfirmedMonthly?.freshness === "current") return `$${gym.operatorConfirmedMonthly.normalizedMonthly.toFixed(0)}/mo`;
  if (gym.reportedMonthly) return `~$${gym.reportedMonthly.point.toFixed(0)}/mo`;
  if (gym.estimatedMonthly) return `~$${gym.estimatedMonthly.point.toFixed(0)}/mo`;
  const context = gym.costContext?.[0];
  if (context) return costContextText(context);
  if (gym.pricingStatus === "free") return "Free/public";
  if (gym.pricingStatus === "pay-per-visit") return "Pay per visit";
  if (gym.pricingStatus === "not-applicable") return "Not applicable";
  return "Needs confirmation";
}

export function pricingStatusText(gym: Gym): string {
  if (gym.costContext?.length && !gym.monthlyPrice && !gym.operatorConfirmedMonthly && !gym.reportedMonthly && !gym.estimatedMonthly) return costContextStatusText(gym.costContext[0]);
  const labels: Record<string, string> = {
    verified: "Official price",
    "official-range": "Official range",
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
