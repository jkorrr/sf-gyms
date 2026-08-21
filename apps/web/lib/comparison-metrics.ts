import { type Gym, venueTypeLabels } from "./demo-data";
import { formatDistanceMiles } from "./geo";
import type { GymCostEstimate } from "./gym-value";

export type ComparisonMetricValue = {
  text: string;
  note?: string;
  best?: boolean;
};

export type ComparisonMetric = {
  key: string;
  label: string;
  values: ComparisonMetricValue[];
};

export type ComparisonMetricGroup = {
  key: string;
  label: string;
  metrics: ComparisonMetric[];
};

function money(value: number | null, suffix = ""): string {
  if (value === null) return "Not listed";
  if (value === 0) return `Free${suffix}`;
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 2 })}${suffix}`;
}

function numericValues(
  values: Array<number | null>,
  format: (value: number | null, index: number) => ComparisonMetricValue,
  highlightBest = false,
): ComparisonMetricValue[] {
  const known = values.filter((value): value is number => value !== null);
  const best = known.length >= 2 ? Math.min(...known) : null;
  return values.map((value, index) => ({
    ...format(value, index),
    best: highlightBest && value !== null && value === best,
  }));
}

function textValues(values: string[]): ComparisonMetricValue[] {
  return values.map((text) => ({ text }));
}

export function buildComparisonMetricGroups(options: {
  gyms: Gym[];
  estimates: GymCostEstimate[];
  distances: Array<number | null>;
  months: number;
}): ComparisonMetricGroup[] {
  const { gyms, estimates, distances, months } = options;
  const monthlyRates = estimates.map((estimate) => estimate.monthlyRate);
  const membershipTotals = estimates.map((estimate) => estimate.membershipTotal);
  const effectiveMonthly = estimates.map((estimate) => estimate.effectiveMonthly);
  const perVisit = estimates.map((estimate) => estimate.membershipCostPerVisit);
  const annualFees = gyms.map((gym) => gym.annualFee);
  const joiningFees = gyms.map((gym) => {
    const listedFees = [gym.enrollmentFee, gym.initiationFee].filter((fee): fee is number => typeof fee === "number");
    return listedFees.length === 0 ? null : listedFees.reduce((total, fee) => total + fee, 0);
  });
  const dayPasses = gyms.map((gym) => gym.dayPassPrice);

  return [
    {
      key: "price",
      label: "Real price",
      metrics: [
        {
          key: "membership-rate",
          label: "Membership rate",
          values: numericValues(monthlyRates, (value, index) => ({
            text: money(value, "/mo"),
            note: !estimates[index].usesUnlimitedRate && value !== null ? "Advertised plan; verify visit limits" : undefined,
          }), true),
        },
        { key: "total", label: `${months}-month total`, values: numericValues(membershipTotals, (value) => ({ text: money(value) }), true) },
        { key: "effective-monthly", label: "Effective monthly", values: numericValues(effectiveMonthly, (value) => ({ text: money(value, "/mo") })) },
        { key: "cost-per-visit", label: "Estimated cost / visit", values: numericValues(perVisit, (value) => ({ text: money(value) }), true) },
        { key: "annual-fee", label: "Annual fee", values: numericValues(annualFees, (value) => ({ text: money(value, "/yr") })) },
        { key: "joining-fee", label: "Joining / initiation", values: numericValues(joiningFees, (value) => ({ text: money(value) })) },
        { key: "day-pass", label: "Day pass", values: numericValues(dayPasses, (value) => ({ text: money(value) }), true) },
      ],
    },
    {
      key: "fit",
      label: "Access and fit",
      metrics: [
        { key: "distance", label: "Distance", values: textValues(distances.map((distance) => distance === null ? "Set a starting area" : formatDistanceMiles(distance))) },
        { key: "venue-type", label: "Venue type", values: textValues(gyms.map((gym) => venueTypeLabels[gym.venueType])) },
        { key: "hours", label: "Hours", values: textValues(gyms.map((gym) => gym.isOpen247 ? "Open 24/7" : gym.hours)) },
        { key: "amenities", label: "Amenities", values: textValues(gyms.map((gym) => gym.amenities.length ? gym.amenities.slice(0, 6).join(" · ") : "Not listed")) },
      ],
    },
    {
      key: "confidence",
      label: "Data confidence",
      metrics: [
        {
          key: "price-status",
          label: "Price status",
          values: textValues(gyms.map((gym) => gym.priceSource
            ? "Official source checked"
            : gym.freshness === "verified" ? "Verified"
              : gym.freshness === "stale" ? "May be stale" : "Not listed")),
        },
        { key: "observed", label: "Observed", values: textValues(gyms.map((gym) => gym.priceObservedAt || "Not listed")) },
        { key: "price-notes", label: "Price notes", values: textValues(gyms.map((gym) => gym.priceNote || "No additional notes")) },
      ],
    },
  ];
}
