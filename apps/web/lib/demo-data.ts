import importedData from "./sf-gyms-osm.json";

export type PriceFreshness = "verified" | "gym_reported" | "stale" | "unknown";
export type PricingStatus = "verified" | "operator-confirmed" | "reported" | "estimated" | "free" | "pay-per-visit" | "not-applicable" | "gated" | "unresolved";
export type EntityKind = "gym" | "studio" | "martial-arts" | "public-recreation" | "outdoor-equipment" | "non-consumer";
export type AccessModel = "membership" | "class-membership" | "class-pack" | "drop-in" | "free-public" | "restricted" | "not-applicable";
export type PublicationStatus = "publish" | "suppress-alias" | "review-hold";

export const venueTypeLabels = {
  traditional_gym: "Traditional & strength gyms",
  boutique_fitness: "Boutique fitness",
  yoga_studio: "Yoga studios",
  pilates_barre: "Pilates, barre & Lagree",
  martial_arts_boxing: "Martial arts & boxing",
  climbing_gym: "Climbing & bouldering",
  gymnastics: "Gymnastics & acrobatics",
  personal_training: "Personal training",
  recreation_sports: "Recreation & sports facilities",
  outdoor_fitness: "Outdoor fitness stations",
  dance_movement: "Dance & movement",
} as const;

export type VenueType = keyof typeof venueTypeLabels;
export const venueTypes = Object.keys(venueTypeLabels) as VenueType[];

export type EstimatedMonthly = {
  point: number;
  low: number;
  high: number;
  currency: "USD";
  confidence: "high" | "medium" | "low";
  basis: string;
  sampleSize: number;
  generatedAt: string;
  estimatorVersion: string;
  rangeMethod?: string;
  validationMedianAbsolutePercentageError?: number;
  validationRangeCoverage?: number;
};

export type ReportedMonthly = {
  point: number;
  low: number;
  high: number;
  currency: "USD";
  confidence: "high" | "medium" | "low";
  conflict: boolean;
  sourceCount: number;
  newestPublishedAt: string;
  basis: string;
  version: string;
};

export type OperatorConfirmedMonthly = {
  amount: number;
  currency: string;
  cadence: string;
  intervalCount: number;
  normalizedMonthly: number;
  planName: string;
  accessScope: string;
  classAllowance?: { count: number | null; period: string; unlimited?: boolean } | null;
  commitment: { type: string; minimumMonths?: number | null; minimumDays?: number | null };
  fees: Array<{ type: string; amount: number; currency: string; cadence: string; mandatory: boolean }>;
  confirmedAt: string;
  contactMethod: string;
  evidenceId: string;
  freshness: "current" | "stale";
  publiclyReproducible: false;
};

export type GymDeal = {
  id: string;
  label: string;
  amount: number;
  currency: string;
  productType: string;
  cadence: string;
  eligibilityLabel: string;
  sourceUrl: string;
  capturedAt: string;
  expiresAt?: string | null;
  contentHash: string;
  freshness: "current";
  replacesOrdinaryPrice: false;
};

export type PriceReport = {
  id: string;
  productType: string;
  amount: number;
  currency: string;
  cadence: string;
  normalizedMonthly: number | null;
  publishedAt: string;
  sourceUrl: string;
  sourcePublisher: string;
  sourceType: string;
  evidenceLabel: string;
  eligibleForSummary: boolean;
};

export type GymPlan = {
  id: string;
  sourceProductId?: string;
  name: string;
  productType: string;
  accessScope: string;
  scopeType?: string;
  classAllowance?: { count: number | null; period: string; unlimited: boolean; disclosed: boolean } | null;
  billing: { amount: number | null; currency: string; interval: string; intervalCount: number; normalizedMonthly: number | null; normalizationFormula?: string };
  commitment?: { type: string; minimumMonths?: number | null; minimumDays?: number | null; rawLabel?: string };
  availability?: string;
  purchaseMethod?: string;
  eligibility?: { type: string; restrictions: string[] };
  promotion?: { isPromotion: boolean; label: string; expiresAt?: string | null };
  fees: Array<{ type: string; amount: number; currency: string; cadence: string; mandatory: boolean }>;
  evidence?: { url: string; observedAt: string; source: string; method: string; rawLabel: string; contentHash: string; evidenceTier?: string; exactLocationMatch?: string; sourceProductId?: string; conflictFlags?: string[] };
  bestValueLabel?: boolean;
  selected: boolean;
  selectionReason: string;
};

export type CostContext = {
  id: string;
  kind: "range" | "starting-price" | string;
  label: string;
  low: number;
  high: number;
  currency: string;
  cadence: string;
  productType: string;
  sourceUrl: string;
  observedAt: string;
  evidenceTier: "official-public";
  selectable: false;
};

export type GymDropIn = {
  id: string;
  sourceProductId?: string;
  name: string;
  productType: "drop-in";
  accessScope: string;
  amount: number;
  currency: string;
  selected: boolean;
  selectionReason: string;
};

export type Gym = {
  id: string;
  databaseId?: string;
  name: string;
  neighborhood: string;
  address: string;
  gymType: string;
  venueType: VenueType;
  latitude: number;
  longitude: number;
  monthlyPrice: number | null;
  monthlyUnlimitedPrice?: number | null;
  annualFee: number | null;
  annualPrepayPrice?: number | null;
  enrollmentFee?: number | null;
  initiationFee?: number | null;
  initiationFeeNote?: string;
  processingFee?: number | null;
  activationFee?: number | null;
  personalTrainingSessionPrice?: number | null;
  dayPassPrice: number | null;
  freshness: PriceFreshness;
  isOpen247: boolean;
  amenities: string[];
  description: string;
  hours: string;
  websiteUrl: string;
  sourceName: string;
  sourceId: string;
  sourceUrl: string;
  importedAt: string;
  priceSource?: string;
  priceSourceUrl?: string;
  priceNote?: string;
  annualFeeNote?: string;
  priceObservedAt?: string;
  planName?: string;
  planScope?: string;
  billingInterval?: string;
  billingIntervalPrice?: number | null;
  recordStatus?: "open" | "coming_soon";
  entityKind?: EntityKind;
  modality?: string;
  operatorKey?: string;
  operatorId?: string;
  operatorLocationId?: string;
  canonicalLocationId?: string;
  canonicalAddress?: string;
  officialUrl?: string;
  sourceAliases?: Array<{ id?: string; name?: string; address?: string; sourceUrl?: string }>;
  publicationStatus?: PublicationStatus;
  accessModel?: AccessModel;
  pricingStatus?: PricingStatus;
  plans?: GymPlan[];
  dropIns?: GymDropIn[];
  selectedPlanId?: string | null;
  typicalPlanId?: string | null;
  highestAccessPlanId?: string | null;
  bestValuePlanId?: string | null;
  planViewStatus?: Record<string, { status: string; reason: string }>;
  selectedDropInId?: string | null;
  selectionReason?: string;
  planValidationErrors?: string[];
  estimatedMonthly?: EstimatedMonthly | null;
  costContext?: CostContext[];
  reportedMonthly?: ReportedMonthly | null;
  operatorConfirmedMonthly?: OperatorConfirmedMonthly | null;
  deals?: GymDeal[];
  priceReports?: PriceReport[];
  accessAvailability?: "waitlist" | "enrollment-paused" | "members-only" | "presale";
  pricingBlocker?: string;
  monthlyPriceBlocker?: string;
  dayPassPriceBlocker?: string;
  metadataStatus?: Record<string, { status: string; reason: string }>;
  catalogStatus?: Record<string, { status: "source-catalog" | "source-fragment" | "selected-only" | "none"; reason: string }>;
  selectionRuleVersion?: string;
};

type ImportedGym = Omit<Gym, "monthlyPrice" | "annualFee" | "dayPassPrice" | "venueType"> & {
  monthlyPrice: number | null;
  annualFee?: number | null;
  dayPassPrice: number | null;
  venueType?: VenueType;
};

function inferVenueType(gym: ImportedGym): VenueType {
  if (gym.venueType) return gym.venueType;
  const haystack = `${gym.entityKind ?? ""} ${gym.modality ?? ""} ${gym.gymType}`.toLowerCase();
  if (haystack.includes("outdoor")) return "outdoor_fitness";
  if (haystack.includes("martial") || haystack.includes("boxing") || haystack.includes("jiu") || haystack.includes("karate")) return "martial_arts_boxing";
  if (haystack.includes("climb") || haystack.includes("boulder")) return "climbing_gym";
  if (haystack.includes("gymnast") || haystack.includes("acrobat")) return "gymnastics";
  if (haystack.includes("yoga")) return "yoga_studio";
  if (haystack.includes("pilates") || haystack.includes("lagree") || haystack.includes("barre")) return "pilates_barre";
  if (haystack.includes("dance") || haystack.includes("pole") || haystack.includes("aerial")) return "dance_movement";
  if (haystack.includes("personal-training") || haystack.includes("trainer-required")) return "personal_training";
  if (haystack.includes("public-recreation") || haystack.includes("non-consumer") || haystack.includes("institutional")) return "recreation_sports";
  if (gym.entityKind === "studio") return "boutique_fitness";
  return "traditional_gym";
}

export const demoGyms: Gym[] = (importedData.gyms as ImportedGym[])
  .filter((gym) => (gym.publicationStatus ?? "publish") === "publish")
  .map((gym) => ({
    ...gym,
    venueType: inferVenueType(gym),
    annualFee: gym.annualFee ?? null,
    amenities: [...gym.amenities],
  }));
