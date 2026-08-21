"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { basePath } from "../lib/config";
import { ALLOWED_MONTHS, buildComparisonParams, parseComparisonParams, readCompareIds, readStoredAssumptions, writeCompareIds, writeStoredAssumptions } from "../lib/compare-state";
import { demoGyms, type Gym, venueTypeLabels } from "../lib/demo-data";
import { estimateGymCost, DEFAULT_COMPARISON_ASSUMPTIONS, type ComparisonAssumptions, type GymCostEstimate } from "../lib/gym-value";
import { distanceMiles, formatDistanceMiles } from "../lib/geo";

const validIds = new Set(demoGyms.map((gym) => gym.id));

function money(value: number | null, suffix = ""): string {
  if (value === null) return "Not listed";
  if (value === 0) return `Free${suffix}`;
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 2 })}${suffix}`;
}

function bestClass(value: number | null, values: Array<number | null>, lower = true): string {
  const known = values.filter((item): item is number => item !== null);
  if (value === null || known.length < 2) return "";
  const best = lower ? Math.min(...known) : Math.max(...known);
  return value === best ? "compare-best" : "";
}

export default function CompareExperience() {
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [assumptions, setAssumptions] = useState<ComparisonAssumptions>(DEFAULT_COMPARISON_ASSUMPTIONS);
  const [hydrated, setHydrated] = useState(false);
  const [search, setSearch] = useState("");
  const [locationQuery, setLocationQuery] = useState("");
  const [locationStatus, setLocationStatus] = useState("");
  const [isSearching, setIsSearching] = useState(false);
  const [message, setMessage] = useState("");
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const parsed = parseComparisonParams(params, validIds);
    setCompareIds(params.has("gyms") ? parsed.ids : readCompareIds(window.localStorage, validIds));
    setAssumptions(parsed.hasAssumptions ? parsed.assumptions : readStoredAssumptions(window.localStorage));
    setHydrated(true);
    return () => controllerRef.current?.abort();
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    writeCompareIds(window.localStorage, compareIds);
    writeStoredAssumptions(window.localStorage, assumptions);
    const params = buildComparisonParams(compareIds, assumptions);
    window.history.replaceState({}, "", `${window.location.pathname}?${params.toString()}`);
  }, [assumptions, compareIds, hydrated]);

  const gyms = useMemo(() => compareIds.map((id) => demoGyms.find((gym) => gym.id === id)).filter((gym): gym is Gym => Boolean(gym)), [compareIds]);
  const estimates = useMemo(() => gyms.map((gym) => estimateGymCost(gym, assumptions)), [assumptions, gyms]);
  const suggestions = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return demoGyms.filter((gym) => !compareIds.includes(gym.id) && (!needle || `${gym.name} ${gym.neighborhood} ${venueTypeLabels[gym.venueType]}`.toLowerCase().includes(needle))).slice(0, 12);
  }, [compareIds, search]);

  const addGym = (id: string) => {
    if (compareIds.includes(id)) return;
    if (compareIds.length >= 3) {
      setMessage("Compare up to three gyms at a time. Remove one before adding another.");
      return;
    }
    setCompareIds((current) => [...current, id]);
    setMessage("");
  };

  const removeGym = (id: string) => {
    setCompareIds((current) => current.filter((item) => item !== id));
    setMessage("");
  };

  const searchLocation = async () => {
    const query = locationQuery.trim();
    if (!query) {
      setLocationStatus("Enter a San Francisco neighborhood, address, or landmark.");
      return;
    }
    controllerRef.current?.abort();
    controllerRef.current = new AbortController();
    setIsSearching(true);
    setLocationStatus("Finding that area...");
    try {
      const params = new URLSearchParams({ q: `${query}, San Francisco, CA`, format: "jsonv2", limit: "1", countrycodes: "us" });
      const response = await fetch(`https://nominatim.openstreetmap.org/search?${params}`, { signal: controllerRef.current.signal, headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error("Location lookup failed");
      const results = await response.json() as Array<{ lat: string; lon: string }>;
      if (!results[0]) {
        setLocationStatus("No matching San Francisco location found.");
        return;
      }
      setAssumptions((current) => ({ ...current, origin: { latitude: Number(results[0].lat), longitude: Number(results[0].lon), label: query } }));
      setLocationStatus(`Distances now use ${query}. Shared links use a rounded location.`);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      setLocationStatus("Location search could not complete. Try a neighborhood name.");
    } finally {
      setIsSearching(false);
    }
  };

  const shareComparison = async () => {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setMessage("Comparison link copied. Precise addresses are not included.");
    } catch {
      setMessage("Copy the current browser URL to share this comparison.");
    }
  };

  const membershipTotals = estimates.map((estimate) => estimate.membershipTotal);
  const perVisitValues = estimates.map((estimate) => estimate.membershipCostPerVisit);
  const distances = gyms.map((gym) => assumptions.origin ? distanceMiles(assumptions.origin, gym) : null);
  const monthlyRates = estimates.map((estimate) => estimate.monthlyRate);
  const dayPasses = gyms.map((gym) => gym.dayPassPrice);

  return (
    <main className={`shell compare-page${hydrated && gyms.length > 0 ? " has-comparison" : ""}`} aria-busy={!hydrated}>
      <header className="topbar">
        <a className="brand" href={`${basePath}/`} aria-label="SFGYMS home">
          <div className="logo brand-mark" aria-hidden="true"><svg className="brand-mark-icon" viewBox="0 0 24 24"><path d="M5.5 8.25v7.5M8.25 6.5v11M10.25 10.75h3.5M15.75 6.5v11M18.5 8.25v7.5M8.25 12h7.5" /></svg></div>
          <div className="brand-lockup"><h1>SFGYMS</h1><p>The independent SF gym guide.</p></div>
        </a>
        <nav className="topnav" aria-label="Primary navigation"><a href={`${basePath}/#map`}>Explore gyms</a><a href={`${basePath}/compare/`} aria-current="page">Compare prices</a></nav>
      </header>

      <section className="compare-hero">
        <div><div className="eyebrow">Gym value simulator</div><h2>Compare the cost of showing up.</h2><p>See mandatory fees, estimated cost per visit, distance, and the point where a membership beats buying day passes.</p></div>
        <button className="secondary" type="button" onClick={() => void shareComparison()}>Share comparison</button>
      </section>

      {!hydrated ? <section className="compare-loading" role="status" aria-live="polite">
        <div className="compare-loading-copy"><span className="compare-loading-dot" aria-hidden="true" /><div><strong>Loading your comparison</strong><small>Restoring your selected gyms and cost assumptions.</small></div></div>
        <div className="compare-loading-bars" aria-hidden="true"><span /><span /><span /></div>
      </section> : <>
      <section className="simulator-controls" aria-label="Comparison assumptions">
        <label>Visits each week<select value={assumptions.visitsPerWeek} onChange={(event) => setAssumptions((current) => ({ ...current, visitsPerWeek: Number(event.target.value) }))}>{Array.from({ length: 14 }, (_, index) => <option key={index + 1} value={index + 1}>{index + 1}</option>)}</select></label>
        <label>Membership horizon<select value={assumptions.months} onChange={(event) => setAssumptions((current) => ({ ...current, months: Number(event.target.value) as ComparisonAssumptions["months"] }))}>{ALLOWED_MONTHS.map((months) => <option key={months} value={months}>{months} month{months === 1 ? "" : "s"}</option>)}</select></label>
        <form className="compare-location" onSubmit={(event) => { event.preventDefault(); void searchLocation(); }}><label htmlFor="compare-location">Starting area</label><input id="compare-location" value={locationQuery} onChange={(event) => setLocationQuery(event.target.value)} placeholder="Potrero Hill or an address" /><button className="secondary" disabled={isSearching} type="submit">{isSearching ? "Finding..." : "Set area"}</button></form>
        <button className="text-button" type="button" onClick={() => { setAssumptions(DEFAULT_COMPARISON_ASSUMPTIONS); setLocationQuery(""); setLocationStatus(""); }}>Reset assumptions</button>
        {assumptions.origin && <span className="location-origin-pill">From <strong>{assumptions.origin.label}</strong></span>}
        {locationStatus && <span className="location-status" role="status">{locationStatus}</span>}
      </section>

      <section className="compare-builder" aria-labelledby="compare-builder-heading">
        <div className="compare-builder-head"><div><div className="section-label">Three-gym shortlist</div><h3 id="compare-builder-heading">Choose gyms to compare</h3></div><span>{gyms.length}/3 selected</span></div>
        <div className="compare-selected-slots">
          {gyms.map((gym) => <article key={gym.id}><strong>{gym.name}</strong><span>{gym.neighborhood}</span><button type="button" onClick={() => removeGym(gym.id)} aria-label={`Remove ${gym.name}`}>×</button></article>)}
          {Array.from({ length: Math.max(0, 3 - gyms.length) }, (_, index) => <article className="empty" key={index}>Add a gym</article>)}
        </div>
        {gyms.length < 3 && <div className="compare-picker"><label><span className="sr-only">Search gyms to compare</span><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search gym, neighborhood, or venue type" /></label><div className="compare-suggestions">{suggestions.map((gym) => <button type="button" key={gym.id} onClick={() => addGym(gym.id)}><span><strong>{gym.name}</strong><small>{gym.neighborhood} · {venueTypeLabels[gym.venueType]}</small></span><span>+</span></button>)}</div></div>}
        {message && <p className="compare-message" role="status">{message}</p>}
      </section>

      {gyms.length > 0 && <>
        <section className="value-summary" aria-label="Comparison highlights">
          <SummaryCard label="Lowest total" gyms={gyms} values={membershipTotals} format={(value) => money(value)} />
          <SummaryCard label="Lowest cost / visit" gyms={gyms} values={perVisitValues} format={(value) => money(value)} />
          {assumptions.origin && <SummaryCard label="Closest" gyms={gyms} values={distances} format={(value) => `${value.toFixed(value < 10 ? 1 : 0)} mi`} />}
          <article><span>Usage assumption</span><strong>{assumptions.visitsPerWeek}× weekly</strong><small>About {Math.round(assumptions.visitsPerWeek * 52 / 12)} visits each month</small></article>
        </section>

        <section className="comparison-matrix" aria-labelledby="comparison-matrix-heading">
          <div className="compare-matrix-head"><div><div className="section-label">Side by side</div><h3 id="comparison-matrix-heading">The real cost and gym fit</h3></div><p>Green cells mark the best known comparable value. Missing prices never win.</p></div>
          <div className="compare-matrix-scroll"><table><thead><tr><th>Factor</th>{gyms.map((gym) => <th key={gym.id}><a href={`${basePath}/gyms/${encodeURIComponent(gym.id)}/`}>{gym.name}</a><small>{gym.neighborhood}</small></th>)}</tr></thead><tbody>
            <GroupRow label="Real price" count={gyms.length} />
            <MatrixRow label="Membership rate" gyms={gyms} values={monthlyRates} render={(value, index) => <span className={bestClass(value, monthlyRates)}>{money(value, "/mo")}{!estimates[index].usesUnlimitedRate && value !== null && <small>Advertised plan; verify visit limits</small>}</span>} />
            <MatrixRow label={`${assumptions.months}-month total`} gyms={gyms} values={membershipTotals} render={(value) => <span className={bestClass(value, membershipTotals)}>{money(value)}</span>} />
            <MatrixRow label="Effective monthly" gyms={gyms} values={estimates.map((item) => item.effectiveMonthly)} render={(value) => money(value, "/mo")} />
            <MatrixRow label="Estimated cost / visit" gyms={gyms} values={perVisitValues} render={(value) => <span className={bestClass(value, perVisitValues)}>{money(value)}</span>} />
            <MatrixRow label="Annual fee" gyms={gyms} values={gyms.map((gym) => gym.annualFee)} render={(value) => money(value, "/yr")} />
            <MatrixRow label="One-time joining fees" gyms={gyms} values={gyms.map((gym) => (gym.enrollmentFee ?? 0) + (gym.initiationFee ?? 0) + (gym.processingFee ?? 0) + (gym.activationFee ?? 0))} render={(value) => money(value)} />
            <MatrixRow label="Day pass" gyms={gyms} values={dayPasses} render={(value) => <span className={bestClass(value, dayPasses)}>{money(value)}</span>} />
            <GroupRow label="Access and fit" count={gyms.length} />
            <TextRow label="Distance" values={distances.map((distance) => distance === null ? "Set a starting area" : formatDistanceMiles(distance))} />
            <TextRow label="Venue type" values={gyms.map((gym) => venueTypeLabels[gym.venueType])} />
            <TextRow label="Hours" values={gyms.map((gym) => gym.isOpen247 ? "Open 24/7" : gym.hours)} />
            <TextRow label="Amenities" values={gyms.map((gym) => gym.amenities.length ? gym.amenities.slice(0, 6).join(" · ") : "Not listed")} />
            <GroupRow label="Data confidence" count={gyms.length} />
            <TextRow label="Price status" values={gyms.map((gym) => gym.priceSource ? "Official source checked" : gym.freshness === "verified" ? "Verified" : gym.freshness === "stale" ? "May be stale" : "Not listed")} />
            <TextRow label="Observed" values={gyms.map((gym) => gym.priceObservedAt ?? "Not listed")} />
            <TextRow label="Price notes" values={gyms.map((gym) => gym.priceNote ?? "No additional notes")} />
          </tbody></table></div>
        </section>

        <section className="break-even-panel" aria-labelledby="break-even-heading"><div><div className="section-label">Membership optimizer</div><h3 id="break-even-heading">When does membership beat day passes?</h3></div><div className="break-even-list">{gyms.map((gym, index) => <BreakEvenRow key={gym.id} gym={gym} estimate={estimates[index]} />)}</div></section>
      </>}
      </>}
    </main>
  );
}

function SummaryCard({ label, gyms, values, format }: { label: string; gyms: Gym[]; values: Array<number | null>; format: (value: number) => string }) {
  const known = values.map((value, index) => ({ value, gym: gyms[index] })).filter((item): item is { value: number; gym: Gym } => item.value !== null);
  const best = known.sort((left, right) => left.value - right.value)[0];
  return <article><span>{label}</span><strong>{best ? best.gym.name : "Not enough data"}</strong><small>{best ? format(best.value) : "Add gyms with published prices"}</small></article>;
}

function GroupRow({ label, count }: { label: string; count: number }) {
  return <tr className="matrix-group"><th>{label}</th><td colSpan={count} /></tr>;
}

function MatrixRow({ label, gyms, values, render }: { label: string; gyms: Gym[]; values: Array<number | null>; render: (value: number | null, index: number) => React.ReactNode }) {
  return <tr><th scope="row">{label}</th>{gyms.map((gym, index) => <td key={gym.id}>{render(values[index], index)}</td>)}</tr>;
}

function TextRow({ label, values }: { label: string; values: string[] }) {
  return <tr><th scope="row">{label}</th>{values.map((value, index) => <td key={`${label}-${index}`}>{value}</td>)}</tr>;
}

function BreakEvenRow({ gym, estimate }: { gym: Gym; estimate: GymCostEstimate }) {
  const breakEven = estimate.breakEvenVisitsPerMonth;
  const width = breakEven === null ? 0 : Math.min(100, breakEven / 30 * 100);
  const recommendation = estimate.recommendation === "membership" ? "Membership is cheaper at your expected usage."
    : estimate.recommendation === "day_pass" ? "Day passes are cheaper at your expected usage."
      : estimate.recommendation === "only_membership" ? "Only membership pricing is publicly listed."
        : estimate.recommendation === "only_day_pass" ? "Only day-pass pricing is publicly listed."
          : "Not enough public pricing to calculate.";
  return <article><div><strong>{gym.name}</strong><span>{breakEven === null ? "Break-even unavailable" : `${breakEven} visits/month to break even`}</span><small>{recommendation}</small></div><div className="break-even-track" aria-hidden="true"><span style={{ width: `${width}%` }} /></div></article>;
}
