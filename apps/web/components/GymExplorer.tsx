"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import GymMap from "./GymMap";
import { appOrigin } from "../lib/config";
import { demoGyms, type Gym } from "../lib/demo-data";
import { distanceMiles, formatDistanceMiles, type GeoPoint } from "../lib/geo";
import { getSupabaseClient } from "../lib/supabase";

type ApiGym = {
  id: string;
  name: string;
  address: string;
  neighborhood?: string | null;
  latitude: number;
  longitude: number;
  gym_type: string;
  is_open_24_7: boolean;
  amenities: string[];
  monthly_price?: number | null;
  day_pass_price?: number | null;
  price_freshness: Gym["freshness"];
  price_source?: string | null;
  price_source_url?: string | null;
  price_note?: string | null;
  price_observed_at?: string | null;
  source_name?: string | null;
  source_id?: string | null;
  source_url?: string | null;
  imported_at?: string | null;
};

type LocationSearchResult = {
  lat: string;
  lon: string;
  display_name: string;
};

type SortOrder = "recommended" | "monthly" | "day_pass" | "distance";

function fromApiGym(gym: ApiGym): Gym {
  const sourceUrl = gym.source_url ?? "https://www.openstreetmap.org/";
  return {
    id: gym.id,
    name: gym.name,
    neighborhood: gym.neighborhood ?? "San Francisco",
    address: gym.address,
    gymType: gym.gym_type,
    latitude: gym.latitude,
    longitude: gym.longitude,
    monthlyPrice: gym.monthly_price ?? null,
    dayPassPrice: gym.day_pass_price ?? null,
    freshness: gym.price_freshness ?? "unknown",
    isOpen247: gym.is_open_24_7,
    amenities: gym.amenities ?? [],
    description: "A San Francisco fitness option. Verify current pricing and hours with the gym.",
    hours: gym.is_open_24_7 ? "Open 24 hours" : "Hours vary",
    websiteUrl: sourceUrl,
    sourceName: gym.source_name ?? "OpenStreetMap",
    sourceId: gym.source_id ?? gym.id,
    sourceUrl,
    importedAt: gym.imported_at ?? "",
    priceSource: gym.price_source ?? undefined,
    priceSourceUrl: gym.price_source_url ?? undefined,
    priceNote: gym.price_note ?? undefined,
    priceObservedAt: gym.price_observed_at ?? undefined,
  };
}

function freshnessLabel(gym: Gym): string {
  if (gym.priceSource) return `Official price checked ${gym.priceObservedAt ?? "recently"}`;
  if (gym.freshness === "verified") return "Price verified recently";
  if (gym.freshness === "gym_reported") return "Price reported by gym";
  if (gym.freshness === "stale") return "Price may be out of date";
  return "Price not listed in source data";
}

function priceLabel(value: number | null, suffix: string): string {
  return value === null ? "Not listed" : `$${value}${suffix}`;
}

function compareNullablePrices(left: number | null, right: number | null): number {
  if (left === null && right === null) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  return left - right;
}

export default function GymExplorer() {
  const [gyms, setGyms] = useState<Gym[]>(demoGyms);
  const [query, setQuery] = useState("");
  const [neighborhoodFilter, setNeighborhoodFilter] = useState("");
  const [maxMonthly, setMaxMonthly] = useState("");
  const [radiusMiles, setRadiusMiles] = useState("");
  const [sortOrder, setSortOrder] = useState<SortOrder>("recommended");
  const [locationQuery, setLocationQuery] = useState("");
  const [origin, setOrigin] = useState<GeoPoint | null>(null);
  const [locationStatus, setLocationStatus] = useState("");
  const [isSearchingLocation, setIsSearchingLocation] = useState(false);
  const [selected, setSelected] = useState<Gym | null>(demoGyms[0] ?? null);
  const [savedIds, setSavedIds] = useState<string[]>([]);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [authMessage, setAuthMessage] = useState("");
  const [authLabel, setAuthLabel] = useState("Sign in with Google");
  const locationControllerRef = useRef<AbortController | null>(null);

  const supabase = getSupabaseClient();

  useEffect(() => {
    const stored = window.localStorage.getItem("sf-gyms:saved");
    if (stored) {
      try {
        const parsed = JSON.parse(stored) as unknown;
        if (Array.isArray(parsed) && parsed.every((item) => typeof item === "string")) setSavedIds(parsed);
      } catch {
        window.localStorage.removeItem("sf-gyms:saved");
      }
    }

    if (!supabase) {
      setAuthMessage("Demo mode is active. Add Supabase keys to enable Google login and cloud saves.");
      return;
    }

    const loadSession = async () => {
      const { data } = await supabase.auth.getSession();
      if (data.session?.user.email) setAuthLabel(data.session.user.email);
    };
    void loadSession();
    const { data } = supabase.auth.onAuthStateChange((_event, session) => {
      setAuthLabel(session?.user.email ?? "Sign in with Google");
    });
    return () => data.subscription.unsubscribe();
  }, [supabase]);

  useEffect(() => {
    const code = new URLSearchParams(window.location.search).get("code");
    const oauthError = new URLSearchParams(window.location.search).get("error_description");
    if (oauthError) {
      setAuthMessage(`Google login could not finish: ${oauthError}`);
      window.history.replaceState({}, "", `${appOrigin()}/`);
      return;
    }
    if (!code || !supabase) return;
    let cancelled = false;
    void supabase.auth.exchangeCodeForSession(code).then(({ error }) => {
      if (cancelled) return;
      window.history.replaceState({}, "", `${appOrigin()}/`);
      setAuthMessage(error ? "Google login expired. Please try again." : "You are signed in.");
    });
    return () => { cancelled = true; };
  }, [supabase]);

  useEffect(() => {
    const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL;
    const isDemo = process.env.NEXT_PUBLIC_DEMO_MODE !== "false";
    if (!apiBase || isDemo) return;
    const controller = new AbortController();
    void fetch(`${apiBase}/api/v1/gyms`, { signal: controller.signal })
      .then((response) => response.ok ? response.json() as Promise<{ items: ApiGym[] }> : Promise.reject(new Error("API unavailable")))
      .then((data) => {
        const next = data.items.map(fromApiGym);
        if (next.length > 0) {
          setGyms(next);
          setSelected(next[0]);
        }
      })
      .catch(() => setAuthMessage("Showing the published OSM listings while the API is unavailable."));
    return () => controller.abort();
  }, []);

  const neighborhoodOptions = useMemo(() => Array.from(new Set(
    gyms
      .map((gym) => gym.neighborhood)
      .filter((neighborhood) => neighborhood && neighborhood !== "San Francisco"),
  )).sort((left, right) => left.localeCompare(right)), [gyms]);

  const visibleRows = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const budget = maxMonthly ? Number(maxMonthly) : Number.POSITIVE_INFINITY;
    const radius = radiusMiles ? Number(radiusMiles) : Number.POSITIVE_INFINITY;
    const rows = gyms
      .map((gym) => ({ gym, distance: origin ? distanceMiles(origin, gym) : null }))
      .filter(({ gym, distance }) => {
        const matchesText = !needle || [gym.name, gym.neighborhood, gym.address, gym.gymType, ...gym.amenities]
          .join(" ").toLowerCase().includes(needle);
        const matchesNeighborhood = !neighborhoodFilter || gym.neighborhood === neighborhoodFilter;
        const matchesBudget = !maxMonthly || (gym.monthlyPrice !== null && gym.monthlyPrice <= budget);
        const matchesRadius = distance === null || distance <= radius;
        return matchesText && matchesNeighborhood && matchesBudget && matchesRadius;
      });

    return rows.sort((left, right) => {
      if (sortOrder === "monthly") {
        return compareNullablePrices(left.gym.monthlyPrice, right.gym.monthlyPrice) || left.gym.name.localeCompare(right.gym.name);
      }
      if (sortOrder === "day_pass") {
        return compareNullablePrices(left.gym.dayPassPrice, right.gym.dayPassPrice) || left.gym.name.localeCompare(right.gym.name);
      }
      if ((sortOrder === "distance" || sortOrder === "recommended") && left.distance !== null && right.distance !== null) {
        return left.distance - right.distance || left.gym.name.localeCompare(right.gym.name);
      }
      return left.gym.name.localeCompare(right.gym.name);
    });
  }, [gyms, maxMonthly, neighborhoodFilter, origin, query, radiusMiles, sortOrder]);

  const filteredGyms = useMemo(() => visibleRows.map(({ gym }) => gym), [visibleRows]);
  const selectedGym = selected && filteredGyms.some((gym) => gym.id === selected.id)
    ? selected
    : filteredGyms[0] ?? null;
  const selectedDistance = selectedGym && origin ? distanceMiles(origin, selectedGym) : null;

  const toggleSaved = (id: string) => {
    setSavedIds((current) => {
      const next = current.includes(id) ? current.filter((item) => item !== id) : [...current, id];
      window.localStorage.setItem("sf-gyms:saved", JSON.stringify(next));
      return next;
    });
  };

  const toggleCompare = (id: string) => {
    setCompareIds((current) => current.includes(id)
      ? current.filter((item) => item !== id)
      : current.length < 3 ? [...current, id] : current);
  };

  const useCurrentLocation = () => {
    if (!navigator.geolocation) {
      setLocationStatus("This browser does not provide location access.");
      return;
    }
    setLocationStatus("Requesting your location...");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setOrigin({ latitude: position.coords.latitude, longitude: position.coords.longitude, label: "Your location" });
        setLocationQuery("");
        setLocationStatus("Distance is calculated in your browser; your coordinates are not sent to SF Gyms.");
      },
      () => setLocationStatus("Location permission was unavailable. Search for a neighborhood or address instead."),
      { enableHighAccuracy: false, maximumAge: 300_000, timeout: 10_000 },
    );
  };

  const searchLocation = async () => {
    const queryValue = locationQuery.trim();
    if (!queryValue) {
      setLocationStatus("Enter a neighborhood, address, or landmark first.");
      return;
    }
    locationControllerRef.current?.abort();
    const controller = new AbortController();
    locationControllerRef.current = controller;
    setIsSearchingLocation(true);
    setLocationStatus("Searching OpenStreetMap for that location...");
    try {
      const params = new URLSearchParams({ q: `${queryValue}, San Francisco, CA`, format: "jsonv2", limit: "1", countrycodes: "us" });
      const response = await fetch(`https://nominatim.openstreetmap.org/search?${params.toString()}`, {
        headers: { Accept: "application/json" },
        referrerPolicy: "origin",
        signal: controller.signal,
      });
      if (!response.ok) throw new Error("Location search failed");
      const results = await response.json() as LocationSearchResult[];
      const first = results[0];
      if (!first) {
        setLocationStatus("No matching San Francisco location found.");
        return;
      }
      setOrigin({ latitude: Number(first.lat), longitude: Number(first.lon), label: first.display_name });
      setLocationStatus("Distances are straight-line estimates. Use a route planner for walking or driving time.");
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setLocationStatus("Location search could not complete. Try again or use your browser location.");
    } finally {
      if (locationControllerRef.current === controller) {
        locationControllerRef.current = null;
        setIsSearchingLocation(false);
      }
    }
  };

  const signIn = async () => {
    if (!supabase) {
      setAuthMessage("Google login is scaffolded but disabled in demo mode. Configure Supabase Auth to enable it.");
      return;
    }
    const { error } = await supabase.auth.signInWithOAuth({ provider: "google", options: { redirectTo: `${appOrigin()}/` } });
    if (error) setAuthMessage(error.message);
  };

  return (
    <main className="shell">
      <header className="topbar">
        <div className="brand">
          <div className="logo" aria-hidden="true">S</div>
          <div><h1>SF Gyms</h1><p>A softer way to find your next gym.</p></div>
        </div>
        <button className="login-button" onClick={() => void signIn()}>{authLabel}</button>
      </header>

      <section className="hero">
        <div>
          <div className="eyebrow">Move in. Look around. Find your fit.</div>
          <h2>Find a gym that feels like your neighborhood.</h2>
          <p className="hero-copy">Explore the San Francisco fitness directory, compare verified rates, and let the map narrow your search by neighborhood, price, or distance.</p>
        </div>
        <div className="hero-note"><strong>Start with a map, not five tabs.</strong> Prices are shown only when a trusted source has supplied them. Listings without pricing stay visible so the directory can grow through gym and member updates.</div>
      </section>

      {authMessage && <div className="auth-message" role="status">{authMessage}</div>}

      <section className="toolbar" aria-label="Gym filters">
        <label className="search"><span aria-hidden="true">Search</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search a gym or amenity" aria-label="Search gyms" /></label>
        <label className="filter">Neighborhood <select value={neighborhoodFilter} onChange={(event) => setNeighborhoodFilter(event.target.value)} aria-label="Neighborhood"><option value="">All neighborhoods</option>{neighborhoodOptions.map((neighborhood) => <option value={neighborhood} key={neighborhood}>{neighborhood}</option>)}</select></label>
        <label className="filter">Under $<input inputMode="numeric" value={maxMonthly} onChange={(event) => setMaxMonthly(event.target.value.replace(/[^0-9]/g, ""))} placeholder="any" aria-label="Maximum monthly price" /> / month</label>
        <label className="filter">Within <select value={radiusMiles} onChange={(event) => setRadiusMiles(event.target.value)} aria-label="Distance radius"><option value="">any distance</option><option value="1">1 mile</option><option value="3">3 miles</option><option value="5">5 miles</option><option value="10">10 miles</option><option value="25">25 miles</option></select></label>
        <label className="filter">Sort <select value={sortOrder} onChange={(event) => setSortOrder(event.target.value as SortOrder)} aria-label="Sort results"><option value="recommended">Recommended</option><option value="monthly">Lowest monthly</option><option value="day_pass">Lowest day pass</option><option value="distance">Nearest</option></select></label>
      </section>

      <section className="location-toolbar" aria-label="Distance from a location">
        <form className="location-search" onSubmit={(event) => { event.preventDefault(); void searchLocation(); }}>
          <label htmlFor="location-query">Distance from</label>
          <input id="location-query" value={locationQuery} onChange={(event) => setLocationQuery(event.target.value)} placeholder="a neighborhood, address, or landmark" />
          <button className="secondary" type="submit" disabled={isSearchingLocation}>{isSearchingLocation ? "Searching..." : "Find location"}</button>
        </form>
        <button className="secondary" type="button" onClick={useCurrentLocation}>Use my location</button>
        {origin && <button className="text-button" type="button" onClick={() => { setOrigin(null); setRadiusMiles(""); setLocationStatus(""); }}>Clear</button>}
        {locationStatus && <span className="location-status" role="status">{locationStatus}</span>}
      </section>

      {compareIds.length > 0 && <div className="compare-bar"><span><strong>{compareIds.length}</strong> gym{compareIds.length === 1 ? "" : "s"} ready to compare.</span><button onClick={() => setCompareIds([])}>Clear comparison</button></div>}

      <section className="explorer map-first-explorer" aria-label="Gym map and listings">
        <div className="map-panel">
          <div className="map-topbar">
            <div><strong>{filteredGyms.length}</strong> matches{neighborhoodFilter ? ` in ${neighborhoodFilter}` : " across San Francisco"}</div>
            <span>Click a dot to inspect</span>
          </div>
          <GymMap gyms={filteredGyms} selectedId={selectedGym?.id} origin={origin} onSelect={setSelected} />
          {filteredGyms.length > 0 && <div className="results-strip" aria-label="Map result previews">
            <div className="results-strip-header"><strong>Preview matches</strong><span>Showing {Math.min(6, filteredGyms.length)} of {filteredGyms.length}</span></div>
            <div className="mini-results">{visibleRows.slice(0, 6).map(({ gym, distance }) => <button key={gym.id} type="button" className={`mini-result ${selectedGym?.id === gym.id ? "active" : ""}`} onClick={() => setSelected(gym)}>
              <span className="mini-result-name">{gym.name}</span>
              <span className="mini-result-meta">{gym.neighborhood} - {priceLabel(gym.monthlyPrice, "/mo")}{distance !== null ? ` - ${formatDistanceMiles(distance)}` : ""}</span>
            </button>)}</div>
          </div>}
          {selectedGym && <aside className="detail" aria-live="polite">
            <div className="card-top"><div><h3>{selectedGym.name}</h3><p className="card-subtitle">{selectedGym.neighborhood} - {selectedGym.gymType}</p></div><button className={`heart ${savedIds.includes(selectedGym.id) ? "saved" : ""}`} aria-label={`${savedIds.includes(selectedGym.id) ? "Remove" : "Save"} ${selectedGym.name}`} onClick={() => toggleSaved(selectedGym.id)}>{savedIds.includes(selectedGym.id) ? "♥" : "♡"}</button></div>
            <p>{selectedGym.description}</p>
            <p><strong>{priceLabel(selectedGym.monthlyPrice, "/mo")}</strong> - {priceLabel(selectedGym.dayPassPrice, " day pass")}<br />{selectedGym.hours}{selectedDistance !== null && <><br /><strong>{formatDistanceMiles(selectedDistance)}</strong> from {origin?.label}</>}</p>
            <div className="price-row">{selectedGym.amenities.slice(0, 4).map((amenity) => <span className="price-pill" key={amenity}>{amenity}</span>)}</div>
            {selectedGym.priceNote && <p className="price-note">{selectedGym.priceNote}</p>}
            <div className="detail-actions"><a className="primary" href={selectedGym.websiteUrl} target="_blank" rel="noreferrer">{selectedGym.websiteUrl === selectedGym.sourceUrl ? "View source listing" : "Visit gym site"}</a><button className="secondary" onClick={() => toggleCompare(selectedGym.id)}>{compareIds.includes(selectedGym.id) ? "Remove from compare" : "Add to compare"}</button></div>
            <p className="source-note">{freshnessLabel(selectedGym)}. {selectedGym.priceSourceUrl && <><a href={selectedGym.priceSourceUrl} target="_blank" rel="noreferrer">See official price source</a>. </>}Listing source: <a href={selectedGym.sourceUrl} target="_blank" rel="noreferrer">{selectedGym.sourceName}</a>. Confirm pricing and hours before visiting.</p>
          </aside>}
        </div>
      </section>

      <footer className="footer"><span>Map tiles: OpenFreeMap / OpenStreetMap - Map data: <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a> - Listings are community source data.</span><span>More cities after the data earns your trust.</span></footer>
    </main>
  );
}
