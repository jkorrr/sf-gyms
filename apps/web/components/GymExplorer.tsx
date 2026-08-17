"use client";

import { useEffect, useMemo, useState } from "react";

import { appOrigin } from "../lib/config";
import { demoGyms, type Gym } from "../lib/demo-data";
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
  price_freshness: "verified" | "gym_reported" | "stale" | "unknown";
};

const baseForPosition = (latitude: number, longitude: number) => ({
  left: Math.min(86, Math.max(14, ((longitude + 122.46) / 0.07) * 100)),
  top: Math.min(82, Math.max(14, ((37.82 - latitude) / 0.08) * 100)),
});

function fromApiGym(gym: ApiGym): Gym {
  return {
    id: gym.id,
    name: gym.name,
    neighborhood: gym.neighborhood ?? "San Francisco",
    address: gym.address,
    gymType: gym.gym_type,
    latitude: gym.latitude,
    longitude: gym.longitude,
    monthlyPrice: Number(gym.monthly_price ?? 0),
    dayPassPrice: Number(gym.day_pass_price ?? 0),
    freshness: gym.price_freshness ?? "unknown",
    isOpen247: gym.is_open_24_7,
    amenities: gym.amenities ?? [],
    description: "A verified San Francisco fitness option. More details will appear as the listing is enriched.",
    hours: gym.is_open_24_7 ? "Open 24 hours" : "Hours vary",
    websiteUrl: "#",
    position: baseForPosition(gym.latitude, gym.longitude),
  };
}

function freshnessLabel(value: Gym["freshness"]): string {
  if (value === "verified") return "Price verified recently";
  if (value === "gym_reported") return "Price reported by gym";
  return "Price needs confirmation";
}

export default function GymExplorer() {
  const [gyms, setGyms] = useState<Gym[]>(demoGyms);
  const [query, setQuery] = useState("");
  const [maxMonthly, setMaxMonthly] = useState("");
  const [selected, setSelected] = useState<Gym | null>(demoGyms[0]);
  const [savedIds, setSavedIds] = useState<string[]>([]);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [authMessage, setAuthMessage] = useState("");
  const [authLabel, setAuthLabel] = useState("Sign in with Google");

  const supabase = getSupabaseClient();

  useEffect(() => {
    const stored = window.localStorage.getItem("sf-gyms:saved");
    if (stored) setSavedIds(JSON.parse(stored) as string[]);

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
      .catch(() => setAuthMessage("Showing demo listings while the API is unavailable."));
    return () => controller.abort();
  }, []);

  const filteredGyms = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const budget = maxMonthly ? Number(maxMonthly) : Number.POSITIVE_INFINITY;
    return gyms.filter((gym) => {
      const matchesText = !needle || [gym.name, gym.neighborhood, gym.address, gym.gymType, ...gym.amenities]
        .join(" ").toLowerCase().includes(needle);
      return matchesText && gym.monthlyPrice <= budget;
    });
  }, [gyms, maxMonthly, query]);

  const toggleSaved = (id: string) => {
    setSavedIds((current) => {
      const next = current.includes(id) ? current.filter((item) => item !== id) : [...current, id];
      window.localStorage.setItem("sf-gyms:saved", JSON.stringify(next));
      return next;
    });
  };

  const toggleCompare = (id: string) => {
    setCompareIds((current) => current.includes(id) ? current.filter((item) => item !== id) : current.length < 3 ? [...current, id] : current);
  };

  const signIn = async () => {
    if (!supabase) {
      setAuthMessage("Google login is scaffolded but disabled in demo mode. Configure Supabase Auth to enable it.");
      return;
    }
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: `${appOrigin()}/` },
    });
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
          <p className="hero-copy">Compare the details that usually take five tabs to find: real prices, day passes, hours, amenities, and the little signals that make a gym feel right.</p>
        </div>
        <div className="hero-note"><strong>Built for the first week in a new city.</strong> Start with a neighborhood, set a budget, and explore your shortlist without committing yet.</div>
      </section>

      {authMessage && <div className="auth-message" role="status">{authMessage}</div>}

      <section className="toolbar" aria-label="Gym filters">
        <label className="search"><span aria-hidden="true">⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search a neighborhood, gym, or amenity" aria-label="Search gyms" /></label>
        <label className="filter">Under $<input inputMode="numeric" value={maxMonthly} onChange={(event) => setMaxMonthly(event.target.value.replace(/[^0-9]/g, ""))} placeholder="any" aria-label="Maximum monthly price" /> / month</label>
      </section>

      {compareIds.length > 0 && <div className="compare-bar"><span><strong>{compareIds.length}</strong> gym{compareIds.length === 1 ? "" : "s"} ready to compare.</span><button onClick={() => setCompareIds([])}>Clear comparison</button></div>}

      <section className="explorer" aria-label="Gym map and listings">
        <div className="list-panel">
          <div className="list-header"><h3>{filteredGyms.length} gyms in the current view</h3><span>Prices shown monthly</span></div>
          {filteredGyms.length === 0 && <div className="empty">No gyms match that search yet. Try a nearby neighborhood or a wider budget.</div>}
          {filteredGyms.map((gym) => (
            <article key={gym.id} className={`gym-card ${selected?.id === gym.id ? "selected" : ""}`} onClick={() => setSelected(gym)} onKeyDown={(event) => { if (event.key === "Enter") setSelected(gym); }} tabIndex={0} role="button">
              <div className="card-top"><div><h4>{gym.name}</h4><p className="card-subtitle">{gym.neighborhood} · {gym.gymType}</p></div><button className={`heart ${savedIds.includes(gym.id) ? "saved" : ""}`} aria-label={`${savedIds.includes(gym.id) ? "Remove" : "Save"} ${gym.name}`} onClick={(event) => { event.stopPropagation(); toggleSaved(gym.id); }}>{savedIds.includes(gym.id) ? "♥" : "♡"}</button></div>
              <p className="card-address">{gym.address}</p>
              <div className="price-row"><span className="price-pill">${gym.monthlyPrice}<small>/mo</small></span><span className="price-pill">${gym.dayPassPrice}<small>day pass</small></span></div>
              <div className={`freshness ${gym.freshness === "stale" ? "stale" : ""}`}>{freshnessLabel(gym.freshness)}</div>
            </article>
          ))}
        </div>

        <div className="map-panel">
          <div className="map-surface" aria-label="Illustrated map of San Francisco with gym locations" role="img">
            <span className="map-label mission">Mission</span><span className="map-label hayes">Hayes Valley</span><span className="map-label north">North Beach</span><span className="map-label city">San Francisco · demo map</span>
            {filteredGyms.map((gym) => <button key={gym.id} className={`pin ${gym.freshness === "stale" ? "stale" : gym.monthlyPrice > 100 ? "expensive" : ""} ${selected?.id === gym.id ? "active" : ""}`} style={{ left: `${gym.position.left}%`, top: `${gym.position.top}%` }} onClick={() => setSelected(gym)} aria-label={`Open ${gym.name}`}>${gym.monthlyPrice}</button>)}
          </div>
          {selected && <aside className="detail" aria-live="polite"><div className="card-top"><div><h3>{selected.name}</h3><p className="card-subtitle">{selected.neighborhood} · {selected.gymType}</p></div><button className={`heart ${savedIds.includes(selected.id) ? "saved" : ""}`} aria-label="Save selected gym" onClick={() => toggleSaved(selected.id)}>{savedIds.includes(selected.id) ? "♥" : "♡"}</button></div><p>{selected.description}</p><p><strong>${selected.monthlyPrice}/mo</strong> · ${selected.dayPassPrice} day pass<br />{selected.hours}</p><div className="price-row">{selected.amenities.slice(0, 4).map((amenity) => <span className="price-pill" key={amenity}>{amenity}</span>)}</div><div className="detail-actions"><a className="primary" href={selected.websiteUrl} target="_blank" rel="noreferrer">Visit gym site</a><button className="secondary" onClick={() => toggleCompare(selected.id)}>{compareIds.includes(selected.id) ? "Remove from compare" : "Add to compare"}</button></div></aside>}
        </div>
      </section>

      <footer className="footer"><span>Prices are informational and marked with their freshness.</span><span>More cities coming after the data earns your trust.</span></footer>
    </main>
  );
}
