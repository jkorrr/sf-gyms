"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import CompareTray from "./CompareTray";
import GymMap from "./GymMap";
import GymMapPreview from "./GymMapPreview";
import RankedResultsDrawer from "./RankedResultsDrawer";
import { readCompareIds, writeCompareIds } from "../lib/compare-state";
import { basePath, oauthRedirectUrl } from "../lib/config";
import { demoGyms, type Gym, type VenueType, venueTypeLabels, venueTypes } from "../lib/demo-data";
import { distanceMiles, formatDistanceMiles, type GeoPoint } from "../lib/geo";
import { DEFAULT_COMPARISON_ASSUMPTIONS, rankGyms, type SortOrder } from "../lib/gym-value";
import { getSupabaseClient, getSupabaseStatus } from "../lib/supabase";

type ApiGym = {
  id: string;
  name: string;
  address: string;
  neighborhood?: string | null;
  latitude: number;
  longitude: number;
  gym_type: string;
  venue_type?: VenueType | null;
  is_open_24_7: boolean;
  amenities: string[];
  monthly_price?: number | null;
  annual_fee?: number | null;
  day_pass_price?: number | null;
  price_freshness: Gym["freshness"];
  price_source?: string | null;
  price_source_url?: string | null;
  price_note?: string | null;
  annual_fee_note?: string | null;
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

function readableOAuthError(value: string): string {
  return value.trim().replace(/\s+/g, " ").slice(0, 240);
}

function fromApiGym(gym: ApiGym): Gym {
  const sourceUrl = gym.source_url ?? "https://www.openstreetmap.org/";
  return {
    id: gym.id,
    name: gym.name,
    neighborhood: gym.neighborhood ?? "San Francisco",
    address: gym.address,
    gymType: gym.gym_type,
    venueType: gym.venue_type ?? "traditional_gym",
    latitude: gym.latitude,
    longitude: gym.longitude,
    monthlyPrice: gym.monthly_price ?? null,
    annualFee: gym.annual_fee ?? null,
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
    annualFeeNote: gym.annual_fee_note ?? undefined,
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
  if (value === 0) return `Free${suffix}`;
  return value === null ? "Not listed" : `$${value}${suffix}`;
}

function gymDetailHref(id: string): string {
  return `${basePath}/gyms/${encodeURIComponent(id)}/`;
}

export default function GymExplorer() {
  const [gyms, setGyms] = useState<Gym[]>(demoGyms);
  const [query, setQuery] = useState("");
  const [selectedNeighborhoods, setSelectedNeighborhoods] = useState<string[]>([]);
  const [selectedVenueTypes, setSelectedVenueTypes] = useState<VenueType[]>([]);
  const [venueParamsLoaded, setVenueParamsLoaded] = useState(false);
  const [maxMonthly, setMaxMonthly] = useState("");
  const [radiusMiles, setRadiusMiles] = useState("");
  const [sortOrder, setSortOrder] = useState<SortOrder>("best_match");
  const [resultsExpanded, setResultsExpanded] = useState(false);
  const [highlightedId, setHighlightedId] = useState<string | null>(null);
  const [locationQuery, setLocationQuery] = useState("");
  const [origin, setOrigin] = useState<GeoPoint | null>(null);
  const [locationStatus, setLocationStatus] = useState("");
  const [isSearchingLocation, setIsSearchingLocation] = useState(false);
  const [selected, setSelected] = useState<Gym | null>(null);
  const [savedIds, setSavedIds] = useState<string[]>([]);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [compareMessage, setCompareMessage] = useState("");
  const [authMessage, setAuthMessage] = useState("");
  const [authLabel, setAuthLabel] = useState("Sign in with Google");
  const locationInputRef = useRef<HTMLInputElement | null>(null);
  const locationControllerRef = useRef<AbortController | null>(null);
  const oauthCallbackRef = useRef(false);

  const supabase = getSupabaseClient();
  const supabaseStatus = getSupabaseStatus();
  const isCloudAuthReady = supabaseStatus.status === "configured" && supabase !== null;

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

    if (!isCloudAuthReady) {
      if (oauthCallbackRef.current) return;
      setAuthMessage(supabaseStatus.message);
      return;
    }

    const loadSession = async () => {
      const { data, error } = await supabase.auth.getSession();
      if (error) {
        setAuthMessage("Supabase could not restore the session. You can continue browsing or try Google sign-in again.");
        return;
      }
      if (data.session?.user.email) setAuthLabel(data.session.user.email);
    };
    void loadSession();
    const { data } = supabase.auth.onAuthStateChange((_event, session) => {
      setAuthLabel(session?.user.email ?? "Sign in with Google");
    });
    return () => data.subscription.unsubscribe();
  }, [isCloudAuthReady, supabase, supabaseStatus.message]);

  useEffect(() => {
    setCompareIds(readCompareIds(window.localStorage, new Set(demoGyms.map((gym) => gym.id))));
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const requested = params.getAll("venue").filter((value): value is VenueType => venueTypes.includes(value as VenueType));
    setSelectedVenueTypes(Array.from(new Set(requested)));
    setVenueParamsLoaded(true);
  }, []);

  useEffect(() => {
    if (!venueParamsLoaded) return;
    const url = new URL(window.location.href);
    url.searchParams.delete("venue");
    selectedVenueTypes.forEach((venueType) => url.searchParams.append("venue", venueType));
    window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }, [selectedVenueTypes, venueParamsLoaded]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const oauthError = params.get("error_description") ?? params.get("error");
    if (code || oauthError) oauthCallbackRef.current = true;
    if (oauthError) {
      setAuthMessage(`Google sign-in could not finish: ${readableOAuthError(oauthError) || "the provider returned an error."}`);
      window.history.replaceState({}, "", oauthRedirectUrl());
      return;
    }
    if (!code) return;

    // The authorization code is single-use. Remove it before the async
    // exchange so refreshes, screenshots, and copied URLs cannot retain it.
    window.history.replaceState({}, "", oauthRedirectUrl());
    if (!isCloudAuthReady || !supabase) {
      setAuthMessage("Google sign-in returned a code, but Supabase is not available in this build. Check the public configuration and try again.");
      return;
    }

    let cancelled = false;
    void supabase.auth.exchangeCodeForSession(code).then(({ error }) => {
      if (cancelled) return;
      setAuthMessage(error ? "Google sign-in expired or was already used. Start sign-in again." : "You are signed in with Google. Saved gyms remain local in this prototype.");
    });
    return () => { cancelled = true; };
  }, [isCloudAuthReady, supabase]);

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

  const venueTypeCounts = useMemo(() => gyms.reduce<Record<VenueType, number>>((counts, gym) => {
    counts[gym.venueType] += 1;
    return counts;
  }, Object.fromEntries(venueTypes.map((venueType) => [venueType, 0])) as Record<VenueType, number>), [gyms]);

  const filteredBaseGyms = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const budget = maxMonthly ? Number(maxMonthly) : Number.POSITIVE_INFINITY;
    const radius = radiusMiles ? Number(radiusMiles) : Number.POSITIVE_INFINITY;
    return gyms
      .map((gym) => ({ gym, distance: origin ? distanceMiles(origin, gym) : null }))
      .filter(({ gym, distance }) => {
        const matchesText = !needle || [gym.name, gym.neighborhood, gym.address, gym.gymType, venueTypeLabels[gym.venueType], ...gym.amenities]
          .join(" ").toLowerCase().includes(needle);
        const matchesNeighborhood = selectedNeighborhoods.length === 0 || selectedNeighborhoods.includes(gym.neighborhood);
        const matchesVenueType = selectedVenueTypes.length === 0 || selectedVenueTypes.includes(gym.venueType);
        const matchesBudget = !maxMonthly || (gym.monthlyPrice !== null && gym.monthlyPrice <= budget);
        const matchesRadius = distance === null || distance <= radius;
        return matchesText && matchesNeighborhood && matchesVenueType && matchesBudget && matchesRadius;
      })
      .map(({ gym }) => gym);
  }, [gyms, maxMonthly, origin, query, radiusMiles, selectedNeighborhoods, selectedVenueTypes]);

  const rankedRows = useMemo(() => rankGyms(filteredBaseGyms, {
    sortOrder,
    query,
    origin,
    assumptions: { ...DEFAULT_COMPARISON_ASSUMPTIONS, origin },
  }), [filteredBaseGyms, origin, query, sortOrder]);
  const filteredGyms = useMemo(() => rankedRows.map(({ gym }) => gym), [rankedRows]);
  const selectedGym = selected && filteredGyms.some((gym) => gym.id === selected.id) ? selected : null;
  const selectedDistance = selectedGym && origin ? distanceMiles(origin, selectedGym) : null;

  useEffect(() => {
    if (!selectedGym) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSelected(null);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [selectedGym]);

  const toggleNeighborhood = (neighborhood: string) => {
    setSelectedNeighborhoods((current) => current.includes(neighborhood)
      ? current.filter((item) => item !== neighborhood)
      : [...current, neighborhood]);
  };

  const toggleVenueType = (venueType: VenueType) => {
    setSelectedVenueTypes((current) => current.includes(venueType)
      ? current.filter((item) => item !== venueType)
      : [...current, venueType]);
  };

  const handleRadiusChange = (value: string) => {
    setRadiusMiles(value);
    if (!value) {
      if (origin) setLocationStatus(`Showing all venues. Distances are measured from ${origin.label}.`);
      return;
    }

    if (origin) {
      setSortOrder("distance");
      setLocationStatus(`Showing venues within ${value} ${value === "1" ? "mile" : "miles"} of ${origin.label}, nearest first.`);
      return;
    }

    setLocationStatus(`To use the ${value}-mile filter, enter a starting location below and select Find location.`);
    window.requestAnimationFrame(() => {
      locationInputRef.current?.focus();
      locationInputRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    });
  };

  const toggleSaved = (id: string) => {
    setSavedIds((current) => {
      const next = current.includes(id) ? current.filter((item) => item !== id) : [...current, id];
      window.localStorage.setItem("sf-gyms:saved", JSON.stringify(next));
      return next;
    });
  };

  const toggleCompare = (id: string) => {
    setCompareIds((current) => {
      const next = current.includes(id)
        ? current.filter((item) => item !== id)
        : current.length < 3 ? [...current, id] : current;
      if (!current.includes(id) && current.length >= 3) {
        setCompareMessage("Compare up to three gyms at a time. Remove one to add another.");
      } else {
        setCompareMessage("");
      }
      writeCompareIds(window.localStorage, next);
      return next;
    });
  };

  const handleSortChange = (value: SortOrder) => {
    if (value === "distance" && !origin) {
      setLocationStatus("Set a starting location before sorting by nearest.");
      window.requestAnimationFrame(() => {
        locationInputRef.current?.focus();
        locationInputRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
      });
      return;
    }
    setSortOrder(value);
    setResultsExpanded(true);
  };

  const selectRankedGym = (id: string) => {
    const gym = filteredGyms.find((item) => item.id === id);
    if (!gym) return;
    setSelected(gym);
    window.requestAnimationFrame(() => document.querySelector(".map-panel")?.scrollIntoView({ behavior: "smooth", block: "end" }));
  };

  const selectMapGym = (gym: Gym | null) => {
    setSelected(gym);
    if (gym) window.requestAnimationFrame(() => document.querySelector(".map-panel")?.scrollIntoView({ behavior: "smooth", block: "end" }));
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
        if (radiusMiles) setSortOrder("distance");
        setLocationStatus(radiusMiles
          ? `Showing venues within ${radiusMiles} ${radiusMiles === "1" ? "mile" : "miles"} of your location, nearest first.`
          : "Distance is calculated in your browser; your coordinates are not sent to SF Gyms.");
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
      setOrigin({ latitude: Number(first.lat), longitude: Number(first.lon), label: queryValue });
      if (radiusMiles) setSortOrder("distance");
      setLocationStatus(radiusMiles
        ? `Showing venues within ${radiusMiles} ${radiusMiles === "1" ? "mile" : "miles"} of ${queryValue}, nearest first.`
        : `Using ${queryValue} as your starting point. Choose a distance to narrow the map.`);
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
    if (!isCloudAuthReady || !supabase) {
      setAuthMessage(supabaseStatus.message);
      return;
    }
    const redirectTo = oauthRedirectUrl();
    if (!redirectTo) {
      setAuthMessage("Google sign-in can only start in the browser. Refresh the page and try again.");
      return;
    }
    const { error } = await supabase.auth.signInWithOAuth({ provider: "google", options: { redirectTo } });
    if (error) setAuthMessage("Google sign-in could not start. Check that Google is enabled in Supabase Auth and that this exact callback URL is allowed.");
  };

  return (
    <main className={`shell${selectedGym ? " has-map-preview" : ""}`}>
      <header className="topbar">
        <a className="brand" href={`${basePath}/`} aria-label="SFGYMS home">
          <div className="logo brand-mark" aria-hidden="true">
            <svg className="brand-mark-icon" viewBox="0 0 24 24" focusable="false">
              <path d="M5.5 8.25v7.5M8.25 6.5v11M10.25 10.75h3.5M15.75 6.5v11M18.5 8.25v7.5M8.25 12h7.5" />
            </svg>
          </div>
          <div className="brand-lockup"><h1>SFGYMS</h1><p>The independent SF gym guide.</p></div>
        </a>
        <nav className="topnav" aria-label="Primary navigation">
          <a href="#map">Explore gyms</a>
          <a href={`${basePath}/compare/`}>Compare prices</a>
          <button className="login-button" onClick={() => void signIn()} disabled={!isCloudAuthReady} title={isCloudAuthReady ? "Sign in with Google" : "Supabase public configuration is not available in this build"}>{isCloudAuthReady ? authLabel : "Google login unavailable"}</button>
        </nav>
      </header>

      <section className="hero">
        <div className="hero-main">
          <div className="eyebrow">The independent SF gym guide</div>
          <h2>Find your<br /><span>iron home.</span></h2>
          <p className="hero-copy">Real equipment details, transparent prices when they are available, and a map that helps you find the right place to train.</p>
          <div className="hero-proof"><span className="proof-dot" aria-hidden="true" />{gyms.length} local listings · free to explore</div>
        </div>
      </section>

      {authMessage && <div className="auth-message" role="status">{authMessage}</div>}

      <section className="toolbar" aria-label="Gym filters">
        <div className="search-row">
          <label className="search search-field"><span className="search-icon" aria-hidden="true">⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Try the Mission, squat racks, or a gym by name" aria-label="Search gyms" /></label>
          {query && <button className="clear-search" type="button" onClick={() => setQuery("")} aria-label="Clear gym search">×</button>}
        </div>
        <div className="filter-row" aria-label="Filter and sort options">
          <details className="neighborhood-select filter-control">
            <summary aria-label="Choose one or more San Francisco neighborhoods">
              <span>{selectedNeighborhoods.length === 0 ? "All neighborhoods" : `${selectedNeighborhoods.length} neighborhood${selectedNeighborhoods.length === 1 ? "" : "s"}`}</span>
              <span className="neighborhood-select-chevron" aria-hidden="true">⌄</span>
            </summary>
            <div className="neighborhood-menu">
              <button className="neighborhood-clear" type="button" onClick={() => setSelectedNeighborhoods([])} disabled={selectedNeighborhoods.length === 0}>Clear selection</button>
              {neighborhoodOptions.map((neighborhood) => <label className="neighborhood-option" key={neighborhood}>
                <input type="checkbox" checked={selectedNeighborhoods.includes(neighborhood)} onChange={() => toggleNeighborhood(neighborhood)} />
                <span>{neighborhood}</span>
              </label>)}
            </div>
          </details>
          <details className="venue-select filter-control">
            <summary aria-label="Choose one or more venue types">
              <span>{selectedVenueTypes.length === 0 ? "All venue types" : `${selectedVenueTypes.length} venue type${selectedVenueTypes.length === 1 ? "" : "s"}`}</span>
              <span className="neighborhood-select-chevron" aria-hidden="true">⌄</span>
            </summary>
            <div className="venue-menu">
              <button className="neighborhood-clear" type="button" onClick={() => setSelectedVenueTypes([])} disabled={selectedVenueTypes.length === 0}>Clear selection</button>
              {venueTypes.map((venueType) => <label className="neighborhood-option" key={venueType}>
                <input type="checkbox" checked={selectedVenueTypes.includes(venueType)} onChange={() => toggleVenueType(venueType)} />
                <span>{venueTypeLabels[venueType]}</span>
                <span className="venue-count">{venueTypeCounts[venueType]}</span>
              </label>)}
            </div>
          </details>
          <label className="filter filter-control">Budget <span className="filter-input-wrap"><span aria-hidden="true">$</span><input id="max-monthly" inputMode="numeric" value={maxMonthly} onChange={(event) => setMaxMonthly(event.target.value.replace(/[^0-9]/g, ""))} placeholder="Any" aria-label="Maximum monthly price" /></span><span className="filter-suffix">/ month</span></label>
          <label className="filter filter-control">Distance <select value={radiusMiles} onChange={(event) => handleRadiusChange(event.target.value)} aria-label="Distance radius" title={origin ? `Filter by distance from ${origin.label}` : "Choose a radius, then set a starting location"}><option value="">Any distance</option><option value="1">1 mile</option><option value="3">3 miles</option><option value="5">5 miles</option><option value="10">10 miles</option><option value="25">25 miles</option></select></label>
          <label className="filter filter-control">Sort by <select value={sortOrder} onChange={(event) => handleSortChange(event.target.value as SortOrder)} aria-label="Sort results"><option value="best_match">Best match</option><option value="first_year_cost">Lowest first-year cost</option><option value="monthly">Lowest monthly price</option><option value="day_pass">Lowest day pass</option><option value="cost_per_visit">Lowest cost per visit</option><option value="distance">Nearest</option><option value="name">Name A–Z</option></select></label>
        </div>
        {selectedNeighborhoods.length > 0 && <div className="active-neighborhoods" aria-label="Selected neighborhoods" aria-live="polite">
          <span className="active-filter-label">Neighborhoods</span>
          {selectedNeighborhoods.map((neighborhood) => <button className="active-filter-chip" type="button" key={neighborhood} onClick={() => toggleNeighborhood(neighborhood)} aria-label={`Remove ${neighborhood} neighborhood filter`}>
            {neighborhood}<span aria-hidden="true">×</span>
          </button>)}
          {selectedNeighborhoods.length > 1 && <button className="active-filter-clear" type="button" onClick={() => setSelectedNeighborhoods([])}>Clear all</button>}
        </div>}
      </section>

      <section className="location-toolbar" aria-label="Distance from a location">
        <form className="location-search" onSubmit={(event) => { event.preventDefault(); void searchLocation(); }}>
          <label htmlFor="location-query">Distance from</label>
          <input ref={locationInputRef} id="location-query" value={locationQuery} onChange={(event) => setLocationQuery(event.target.value)} placeholder="a neighborhood, address, or landmark" />
          <button className="secondary" type="submit" disabled={isSearchingLocation}>{isSearchingLocation ? "Searching..." : "Find location"}</button>
        </form>
        <button className="secondary" type="button" onClick={useCurrentLocation}>Use my location</button>
        {origin && <span className="location-origin-pill">From <strong>{origin.label}</strong>{radiusMiles && <> · within {radiusMiles} mi</>}</span>}
        {origin && <button className="text-button" type="button" onClick={() => { setOrigin(null); setRadiusMiles(""); setLocationStatus(""); }}>Clear</button>}
        {locationStatus && <span className="location-status" role="status">{locationStatus}</span>}
      </section>

      <section className="explorer map-first-explorer map-section" id="map" aria-label="Gym map and listings">
        <div className="map-heading-row">
          <div><div className="eyebrow">Explore the city</div><h3>{filteredGyms.length} fitness venues in San Francisco</h3></div>
          <span className="map-hint">Pan, zoom, and click a dot to inspect</span>
        </div>
        <div className="map-panel">
          <GymMap gyms={filteredBaseGyms} selectedId={selectedGym?.id} highlightedId={highlightedId} origin={origin} onSelect={selectMapGym} />
          {selectedGym && <GymMapPreview
            gym={selectedGym}
            distance={selectedDistance}
            originLabel={origin?.label}
            isSaved={savedIds.includes(selectedGym.id)}
            isCompared={compareIds.includes(selectedGym.id)}
            onClose={() => setSelected(null)}
            onToggleSave={() => void toggleSaved(selectedGym.id)}
            onToggleCompare={() => toggleCompare(selectedGym.id)}
          />}
        </div>
        <RankedResultsDrawer rows={rankedRows} sortOrder={sortOrder} expanded={resultsExpanded} compareIds={compareIds} onToggle={() => setResultsExpanded((current) => !current)} onSelect={selectRankedGym} onCompare={toggleCompare} onHighlight={setHighlightedId} />
      </section>

      {compareMessage && <p className="global-compare-message" role="status">{compareMessage}</p>}
      {!selectedGym && <CompareTray gyms={gyms} compareIds={compareIds} onRemove={toggleCompare} />}

      <footer className="footer"><span>Map tiles: OpenFreeMap / OpenStreetMap - Map data: <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noreferrer">OpenStreetMap contributors</a> - Listings are community source data.</span><span>More cities after the data earns your trust.</span></footer>
    </main>
  );
}
