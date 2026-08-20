"use client";

import type { RankedGym, SortOrder } from "../lib/gym-value";
import { venueTypeLabels } from "../lib/demo-data";

const sortLabels: Record<SortOrder, string> = {
  best_match: "Best match",
  first_year_cost: "Lowest first-year cost",
  monthly: "Lowest monthly price",
  day_pass: "Lowest day pass",
  cost_per_visit: "Lowest cost per visit",
  distance: "Nearest",
  name: "Name A–Z",
};

type RankedResultsDrawerProps = {
  rows: RankedGym[];
  sortOrder: SortOrder;
  expanded: boolean;
  compareIds: string[];
  onToggle: () => void;
  onSelect: (id: string) => void;
  onCompare: (id: string) => void;
  onHighlight: (id: string | null) => void;
};

function primaryValue(row: RankedGym, sortOrder: SortOrder): string {
  const value = sortOrder === "first_year_cost" ? row.estimate.membershipTotal
    : sortOrder === "monthly" ? row.estimate.monthlyRate
      : sortOrder === "day_pass" ? row.gym.dayPassPrice
        : sortOrder === "cost_per_visit" ? row.estimate.membershipCostPerVisit
          : null;
  if (value !== null) return `$${value < 100 ? value.toFixed(value % 1 ? 2 : 0) : Math.round(value).toLocaleString("en-US")}`;
  if (row.distance !== null && sortOrder === "distance") return `${row.distance.toFixed(row.distance < 10 ? 1 : 0)} mi`;
  if (row.estimate.monthlyRate !== null) return `$${row.estimate.monthlyRate}/mo`;
  return "Price unknown";
}

export default function RankedResultsDrawer(props: RankedResultsDrawerProps) {
  return (
    <section className={`ranked-drawer ${props.expanded ? "expanded" : ""}`} aria-labelledby="ranked-results-heading">
      <button className="ranked-drawer-toggle" type="button" onClick={props.onToggle} aria-expanded={props.expanded}>
        <span><strong id="ranked-results-heading">Ranked results</strong><small>{sortLabels[props.sortOrder]} · {props.rows.length} matches</small></span>
        <span aria-hidden="true">{props.expanded ? "⌄" : "⌃"}</span>
      </button>
      {props.expanded && <div className="ranked-card-rail">
        {props.rows.slice(0, 20).map((row) => <article
          className="ranked-card"
          key={row.gym.id}
          onMouseEnter={() => props.onHighlight(row.gym.id)}
          onMouseLeave={() => props.onHighlight(null)}
          onFocus={() => props.onHighlight(row.gym.id)}
          onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) props.onHighlight(null); }}
        >
          <button className="ranked-card-main" type="button" onClick={() => props.onSelect(row.gym.id)}>
            <span className="rank-number">#{row.rank}</span>
            <span className="ranked-card-copy"><strong>{row.gym.name}</strong><small>{row.gym.neighborhood} · {venueTypeLabels[row.gym.venueType]}</small><span>{row.why}</span></span>
            <span className="ranked-card-value">{primaryValue(row, props.sortOrder)}</span>
          </button>
          <button className="ranked-card-compare" type="button" onClick={() => props.onCompare(row.gym.id)} aria-pressed={props.compareIds.includes(row.gym.id)}>
            {props.compareIds.includes(row.gym.id) ? "Added" : "+ Compare"}
          </button>
        </article>)}
      </div>}
    </section>
  );
}

