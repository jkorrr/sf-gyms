"use client";

import { basePath } from "../lib/config";
import { type Gym, venueTypeLabels } from "../lib/demo-data";
import { formatDistanceMiles } from "../lib/geo";

type GymMapPreviewProps = {
  gym: Gym;
  distance: number | null;
  originLabel?: string;
  isSaved: boolean;
  isCompared: boolean;
  onClose: () => void;
  onToggleSave: () => void;
  onToggleCompare: () => void;
};

function priceLabel(value: number | null | undefined, suffix: string): string {
  if (value === 0) return `Free${suffix}`;
  return value == null ? "Not listed" : `$${value.toLocaleString("en-US", { maximumFractionDigits: 2 })}${suffix}`;
}

function detailLabel(value: string | null | undefined, fallback = "Not listed"): string {
  return value?.trim() || fallback;
}

function freshnessLabel(gym: Gym): string {
  if (gym.priceSource) return `Official price checked ${gym.priceObservedAt || "recently"}`;
  if (gym.freshness === "verified") return "Price verified recently";
  if (gym.freshness === "gym_reported") return "Price reported by gym";
  if (gym.freshness === "stale") return "Price may be out of date";
  return "Public price not yet listed";
}

export default function GymMapPreview({
  gym,
  distance,
  originLabel,
  isSaved,
  isCompared,
  onClose,
  onToggleSave,
  onToggleCompare,
}: GymMapPreviewProps) {
  const amenities = Array.from(new Set(gym.amenities.flatMap((amenity) => amenity.split(";")).map((amenity) => amenity.trim()).filter(Boolean)));
  const detailHref = `${basePath}/gyms/${encodeURIComponent(gym.id)}/`;
  const isSourceOnly = gym.websiteUrl === gym.sourceUrl;
  const prices = [
    { label: "Base monthly", value: priceLabel(gym.monthlyPrice, "/mo") },
    { label: "Unlimited monthly", value: priceLabel(gym.monthlyUnlimitedPrice, "/mo") },
    { label: "Annual fee", value: priceLabel(gym.annualFee, "/yr") },
    { label: "Annual prepaid", value: priceLabel(gym.annualPrepayPrice, "/yr") },
    { label: "Enrollment fee", value: priceLabel(gym.enrollmentFee, "") },
    { label: "Initiation fee", value: priceLabel(gym.initiationFee, "") },
    { label: "Day pass", value: priceLabel(gym.dayPassPrice, "") },
    { label: "Personal training", value: priceLabel(gym.personalTrainingSessionPrice, "/session") },
  ];

  return (
    <aside className="map-preview" role="dialog" aria-modal="false" aria-labelledby="map-preview-title" aria-describedby="map-preview-summary">
      <button className="map-preview-close" type="button" onClick={onClose} aria-label={`Close ${gym.name} preview`}>×</button>
      <div className="map-preview-heading">
        <div>
          <span className="venue-badge">{venueTypeLabels[gym.venueType]}</span>
          <h3 id="map-preview-title">{gym.name}</h3>
          <p>{gym.neighborhood} · {gym.gymType}</p>
        </div>
        <button className={`heart ${isSaved ? "saved" : ""}`} type="button" aria-label={`${isSaved ? "Remove" : "Save"} ${gym.name}`} onClick={onToggleSave}>{isSaved ? "♥" : "♡"}</button>
      </div>

      <p className="map-preview-description" id="map-preview-summary">{detailLabel(gym.description, "No description has been published for this venue.")}</p>

      <div className="map-preview-prices" aria-label="All published prices">
        {prices.map((price) => <div key={price.label}><span>{price.label}</span><strong>{price.value}</strong></div>)}
      </div>

      <div className="map-preview-facts">
        <p><strong>Address</strong><span>{detailLabel(gym.address)}</span></p>
        <p><strong>Hours</strong><span>{gym.isOpen247 ? "Open 24/7" : detailLabel(gym.hours)}</span></p>
        {distance !== null && <p><strong>Distance</strong><span>{formatDistanceMiles(distance)} from {originLabel ?? "your starting point"}</span></p>}
      </div>

      {amenities.length > 0 && <div className="map-preview-amenities" aria-label="All amenities">
        {amenities.map((amenity) => <span key={amenity}>{amenity}</span>)}
      </div>}

      <dl className="map-preview-source-details">
        <div><dt>Price status</dt><dd>{freshnessLabel(gym)}</dd></div>
        <div><dt>Price checked</dt><dd>{detailLabel(gym.priceObservedAt)}</dd></div>
        <div><dt>Price notes</dt><dd>{detailLabel(gym.priceNote, "No additional price notes published.")}</dd></div>
        <div><dt>Annual-fee notes</dt><dd>{detailLabel(gym.annualFeeNote, "No additional annual-fee notes published.")}</dd></div>
        <div><dt>Initiation notes</dt><dd>{detailLabel(gym.initiationFeeNote, "No additional initiation-fee notes published.")}</dd></div>
        <div><dt>Price source</dt><dd>{gym.priceSourceUrl ? <a href={gym.priceSourceUrl} target="_blank" rel="noreferrer">{detailLabel(gym.priceSource, "Official price source")}</a> : detailLabel(gym.priceSource)}</dd></div>
        <div><dt>Listing source</dt><dd>{gym.sourceUrl ? <a href={gym.sourceUrl} target="_blank" rel="noreferrer">{detailLabel(gym.sourceName, "Source listing")}</a> : detailLabel(gym.sourceName)}</dd></div>
        <div><dt>Source reference</dt><dd>{detailLabel(gym.sourceId)}</dd></div>
        <div><dt>Imported</dt><dd>{detailLabel(gym.importedAt)}</dd></div>
      </dl>

      <div className="map-preview-actions">
        <a className="primary" href={detailHref}>Open full listing</a>
        <button className="secondary" type="button" onClick={onToggleCompare}>{isCompared ? "Remove from compare" : "Add to compare"}</button>
        {gym.websiteUrl && <a className="text-button" href={gym.websiteUrl} target="_blank" rel="noreferrer">{isSourceOnly ? "View source listing" : "Visit gym site"}</a>}
        {gym.priceSourceUrl && gym.priceSourceUrl !== gym.websiteUrl && <a className="text-button" href={gym.priceSourceUrl} target="_blank" rel="noreferrer">View official price source</a>}
      </div>
    </aside>
  );
}
