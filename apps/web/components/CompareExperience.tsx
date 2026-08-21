"use client";

import { Fragment, useEffect, useMemo, useRef, useState } from "react";

import GymCombobox from "./GymCombobox";
import { basePath } from "../lib/config";
import {
  ALLOWED_MONTHS,
  buildComparisonParams,
  compactCompareSlots,
  createCompareSlots,
  parseComparisonParams,
  readCompareIds,
  readStoredAssumptions,
  removeCompareSlot,
  setCompareSlot,
  writeCompareIds,
  writeStoredAssumptions,
} from "../lib/compare-state";
import { buildComparisonMetricGroups, type ComparisonMetricValue } from "../lib/comparison-metrics";
import { demoGyms, type Gym } from "../lib/demo-data";
import { distanceMiles } from "../lib/geo";
import { estimateGymCost, DEFAULT_COMPARISON_ASSUMPTIONS, type ComparisonAssumptions, type GymCostEstimate } from "../lib/gym-value";

const validIds = new Set(demoGyms.map((gym) => gym.id));
const gymsById = new Map(demoGyms.map((gym) => [gym.id, gym]));

function money(value: number | null, suffix = ""): string {
  if (value === null) return "Not listed";
  if (value === 0) return `Free${suffix}`;
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 2 })}${suffix}`;
}

export default function CompareExperience() {
  const [compareSlots, setCompareSlots] = useState(createCompareSlots);
  const [assumptions, setAssumptions] = useState<ComparisonAssumptions>(DEFAULT_COMPARISON_ASSUMPTIONS);
  const [hydrated, setHydrated] = useState(false);
  const [activeSlot, setActiveSlot] = useState<number | null>(null);
  const [locationQuery, setLocationQuery] = useState("");
  const [locationStatus, setLocationStatus] = useState("");
  const [isSearching, setIsSearching] = useState(false);
  const [message, setMessage] = useState("");
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const parsed = parseComparisonParams(params, validIds);
    setCompareSlots(createCompareSlots(params.has("gyms") ? parsed.ids : readCompareIds(window.localStorage, validIds)));
    setAssumptions(parsed.hasAssumptions ? parsed.assumptions : readStoredAssumptions(window.localStorage));
    setHydrated(true);
    return () => controllerRef.current?.abort();
  }, []);

  const compareIds = useMemo(() => compactCompareSlots(compareSlots), [compareSlots]);
  const slotGyms = useMemo(() => compareSlots.map((id) => id ? gymsById.get(id) : undefined), [compareSlots]);

  useEffect(() => {
    if (!hydrated) return;
    writeCompareIds(window.localStorage, compareIds);
    writeStoredAssumptions(window.localStorage, assumptions);
    const params = buildComparisonParams(compareIds, assumptions);
    window.history.replaceState({}, "", `${window.location.pathname}?${params.toString()}`);
  }, [assumptions, compareIds, hydrated]);

  const gyms = useMemo(() => slotGyms.filter((gym): gym is Gym => Boolean(gym)), [slotGyms]);
  const estimates = useMemo(() => gyms.map((gym) => estimateGymCost(gym, assumptions)), [assumptions, gyms]);
  const distances = useMemo(() => gyms.map((gym) => assumptions.origin ? distanceMiles(assumptions.origin, gym) : null), [assumptions.origin, gyms]);
  const metricGroups = useMemo(() => buildComparisonMetricGroups({
    gyms,
    estimates,
    distances,
    months: assumptions.months,
  }), [assumptions.months, distances, estimates, gyms]);

  const chooseGym = (slotIndex: number, gymId: string) => {
    setCompareSlots((current) => setCompareSlot(current, slotIndex, gymId));
    setActiveSlot(null);
    setMessage("");
  };

  const removeGym = (slotIndex: number) => {
    setCompareSlots((current) => removeCompareSlot(current, slotIndex));
    setActiveSlot(null);
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
            {[0, 1, 2].map((slotIndex) => {
              const currentGym = slotGyms[slotIndex];
              const excludedIds = new Set(compareSlots.filter((id, index): id is string => index !== slotIndex && Boolean(id)));
              return <GymCombobox
                key={slotIndex}
                slotIndex={slotIndex}
                gyms={demoGyms}
                currentGym={currentGym}
                excludedIds={excludedIds}
                isOpen={activeSlot === slotIndex}
                onOpen={() => setActiveSlot(slotIndex)}
                onClose={() => setActiveSlot((current) => current === slotIndex ? null : current)}
                onSelect={chooseGym}
                onRemove={removeGym}
              />;
            })}
          </div>
          {gyms.length < 2 && <p className="compare-more-prompt" role="status">Add {gyms.length === 0 ? "two gyms" : "one more gym"} to unlock a true side-by-side comparison. Available values still appear below.</p>}
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
            <div className="compare-matrix-scroll">
              <table>
                <thead><tr><th>Factor</th>{gyms.map((gym) => <th key={gym.id}><a href={`${basePath}/gyms/${encodeURIComponent(gym.id)}/`}>{gym.name}</a><small>{gym.neighborhood}</small></th>)}</tr></thead>
                <tbody>{metricGroups.map((group) => <Fragment key={group.key}>
                  <GroupRow label={group.label} count={gyms.length} />
                  {group.metrics.map((metric) => <MetricRow key={metric.key} label={metric.label} gyms={gyms} values={metric.values} />)}
                </Fragment>)}</tbody>
              </table>
            </div>

            <div className="comparison-mobile">
              {metricGroups.map((group) => <section className="comparison-mobile-group" key={group.key} aria-labelledby={`mobile-group-${group.key}`}>
                <h4 id={`mobile-group-${group.key}`}>{group.label}</h4>
                {group.metrics.map((metric) => <article className="comparison-metric-card" key={metric.key}>
                  <h5>{metric.label}</h5>
                  <dl>{gyms.map((gym, index) => <div className={metric.values[index].best ? "best" : ""} key={gym.id}>
                    <dt><a href={`${basePath}/gyms/${encodeURIComponent(gym.id)}/`}>{gym.name}</a><small>{gym.neighborhood}</small></dt>
                    <dd>{metric.values[index].text}{metric.values[index].note && <small>{metric.values[index].note}</small>}</dd>
                  </div>)}</dl>
                </article>)}
              </section>)}
            </div>
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

function MetricRow({ label, gyms, values }: { label: string; gyms: Gym[]; values: ComparisonMetricValue[] }) {
  return <tr><th scope="row">{label}</th>{gyms.map((gym, index) => {
    const value = values[index];
    return <td key={gym.id}><span className={value.best ? "compare-best" : ""}>{value.text}{value.note && <small>{value.note}</small>}</span></td>;
  })}</tr>;
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
