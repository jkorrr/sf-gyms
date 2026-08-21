import type { Gym } from "./demo-data";

export type ExperienceTimeBucket = "early_morning" | "morning" | "midday" | "evening" | "late_night";
export type ExperienceRelationship = "member" | "former_member" | "trial" | "day_pass" | "guest" | "other";

export type ExperienceReport = {
  id: string;
  gym_location_id: string;
  visit_date: string;
  time_bucket?: ExperienceTimeBucket | null;
  relationship: ExperienceRelationship;
  equipment_availability?: "available" | "short_wait" | "long_wait" | "not_available" | null;
  crowding?: "quiet" | "moderate" | "busy" | "packed" | null;
  cleanliness?: "needs_attention" | "acceptable" | "clean" | "exceptionally_clean" | null;
  value_assessment?: "poor" | "fair" | "good" | "excellent" | null;
  billing_transparency?: "unclear" | "partly_clear" | "clear" | null;
  listing_accuracy?: "inaccurate" | "partly_accurate" | "accurate" | null;
  body?: string | null;
  published_at: string;
};

export type ExperienceReportPage = {
  items: ExperienceReport[];
  next_cursor?: string | null;
  demo_mode: boolean;
};

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

const labels = {
  equipment_availability: {
    available: "Equipment available",
    short_wait: "Short equipment wait",
    long_wait: "Long equipment wait",
    not_available: "Equipment unavailable",
  },
  crowding: {
    quiet: "Quiet",
    moderate: "Moderately busy",
    busy: "Busy",
    packed: "Packed",
  },
  cleanliness: {
    needs_attention: "Cleanliness needs attention",
    acceptable: "Acceptably clean",
    clean: "Clean",
    exceptionally_clean: "Exceptionally clean",
  },
  value_assessment: {
    poor: "Poor value",
    fair: "Fair value",
    good: "Good value",
    excellent: "Excellent value",
  },
  billing_transparency: {
    unclear: "Billing unclear",
    partly_clear: "Billing partly clear",
    clear: "Billing clear",
  },
  listing_accuracy: {
    inaccurate: "Listing inaccurate",
    partly_accurate: "Listing partly accurate",
    accurate: "Listing accurate",
  },
} as const;

const relationshipLabels: Record<ExperienceRelationship, string> = {
  member: "Current member",
  former_member: "Former member",
  trial: "Trial visit",
  day_pass: "Day-pass visit",
  guest: "Guest visit",
  other: "Visitor",
};

const timeLabels: Record<ExperienceTimeBucket, string> = {
  early_morning: "early morning",
  morning: "morning",
  midday: "midday",
  evening: "evening",
  late_night: "late night",
};

export function reviewLocationId(gym: Gym): string | undefined {
  if (gym.databaseId && UUID_PATTERN.test(gym.databaseId)) return gym.databaseId;
  return UUID_PATTERN.test(gym.id) ? gym.id : undefined;
}

export function experienceSignalLabels(report: ExperienceReport): string[] {
  const result: string[] = [];
  for (const key of Object.keys(labels) as Array<keyof typeof labels>) {
    const value = report[key];
    if (value) result.push((labels[key] as Record<string, string>)[value]);
  }
  return result;
}

export function experienceContext(report: ExperienceReport): string {
  const relationship = relationshipLabels[report.relationship];
  const time = report.time_bucket ? ` · ${timeLabels[report.time_bucket]}` : "";
  const date = new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(new Date(`${report.visit_date}T00:00:00Z`));
  return `${relationship} · visited ${date}${time}`;
}
