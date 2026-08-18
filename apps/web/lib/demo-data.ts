import importedData from "./sf-gyms-osm.json";

export type PriceFreshness = "verified" | "gym_reported" | "stale" | "unknown";

export type Gym = {
  id: string;
  name: string;
  neighborhood: string;
  address: string;
  gymType: string;
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
