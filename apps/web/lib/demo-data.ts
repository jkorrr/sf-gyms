export type Gym = {
  id: string;
  name: string;
  neighborhood: string;
  address: string;
  gymType: string;
  latitude: number;
  longitude: number;
  monthlyPrice: number;
  dayPassPrice: number;
  freshness: "verified" | "gym_reported" | "stale" | "unknown";
  isOpen247: boolean;
  amenities: string[];
  description: string;
  hours: string;
  websiteUrl: string;
  position: { left: number; top: number };
};

export const demoGyms: Gym[] = [
  {
    id: "11111111-1111-4111-8111-111111111111",
    name: "Mission Strength Co.",
    neighborhood: "Mission",
    address: "2200 Mission Street",
    gymType: "Strength gym",
    latitude: 37.7614,
    longitude: -122.4181,
    monthlyPrice: 89,
    dayPassPrice: 20,
    freshness: "verified",
    isOpen247: true,
    amenities: ["Free weights", "Squat racks", "Showers", "24/7 access"],
    description: "A welcoming strength-focused gym with serious equipment and a neighborhood feel.",
    hours: "Open 24 hours",
    websiteUrl: "https://example.com/mission-strength",
    position: { left: 31, top: 66 },
  },
  {
    id: "22222222-2222-4222-8222-222222222222",
    name: "Hayes Valley Movement",
    neighborhood: "Hayes Valley",
    address: "480 Hayes Street",
    gymType: "Boutique fitness",
    latitude: 37.7765,
    longitude: -122.4248,
    monthlyPrice: 139,
    dayPassPrice: 30,
    freshness: "gym_reported",
    isOpen247: false,
    amenities: ["Classes", "Sauna", "Showers", "Yoga"],
    description: "Small-group classes and open-gym hours in a bright, calm studio.",
    hours: "6:00 AM–9:00 PM weekdays",
    websiteUrl: "https://example.com/hayes-movement",
    position: { left: 49, top: 43 },
  },
  {
    id: "33333333-3333-4333-8333-333333333333",
    name: "North Beach Community Gym",
    neighborhood: "North Beach",
    address: "1450 Stockton Street",
    gymType: "Community gym",
    latitude: 37.7999,
    longitude: -122.4089,
    monthlyPrice: 49,
    dayPassPrice: 12,
    freshness: "stale",
    isOpen247: false,
    amenities: ["Cardio", "Free weights", "Basketball", "Student discount"],
    description: "An affordable local option with broad equipment and court access.",
    hours: "5:00 AM–10:00 PM weekdays",
    websiteUrl: "https://example.com/north-beach-gym",
    position: { left: 72, top: 17 },
  },
];
