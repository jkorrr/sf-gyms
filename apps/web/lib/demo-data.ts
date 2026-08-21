import importedData from "./sf-gyms-osm.json";

export type PriceFreshness = "verified" | "gym_reported" | "stale" | "unknown";

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
};

type ImportedGym = Omit<Gym, "monthlyPrice" | "annualFee" | "dayPassPrice"> & {
  monthlyPrice: number | null;
  annualFee?: number | null;
  dayPassPrice: number | null;
};

export const demoGyms: Gym[] = (importedData.gyms as ImportedGym[]).map((gym) => ({
  ...gym,
  annualFee: gym.annualFee ?? null,
  amenities: [...gym.amenities],
}));
