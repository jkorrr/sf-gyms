import GymDetailActions from "../../../components/GymDetailActions";
import GymExperienceReports from "../../../components/GymExperienceReports";
import { basePath } from "../../../lib/config";
import { demoGyms, venueTypeLabels } from "../../../lib/demo-data";
import { getGymById, monthlyCostText, priceFreshnessText, priceText, pricingStatusText, safeExternalUrl } from "../../../lib/gym-detail";
import { reviewLocationId } from "../../../lib/experience-reports";
import { notFound } from "next/navigation";

type GymPageProps = {
  params: Promise<{ id: string }>;
};

export function generateStaticParams() {
  return demoGyms.map((gym) => ({ id: gym.id }));
}

export const dynamicParams = false;

export default async function GymPage({ params }: GymPageProps) {
  const { id } = await params;
  const gym = getGymById(id);
  if (!gym) notFound();

  const websiteUrl = safeExternalUrl(gym.websiteUrl);
  const listingUrl = safeExternalUrl(gym.sourceUrl);
  const priceSourceUrl = safeExternalUrl(gym.priceSourceUrl);
  const selectedPlan = (gym.plans ?? []).find((plan) => plan.id === gym.selectedPlanId);
  const typicalPlan = (gym.plans ?? []).find((plan) => plan.id === gym.typicalPlanId);
  const highestAccessPlan = (gym.plans ?? []).find((plan) => plan.id === gym.highestAccessPlanId);
  const bestValuePlan = (gym.plans ?? []).find((plan) => plan.id === gym.bestValuePlanId);
  const selectedDropIn = (gym.dropIns ?? []).find((offer) => offer.id === gym.selectedDropInId);
  const reportedSources = (gym.priceReports ?? [])
    .filter((report) => report.eligibleForSummary && report.productType === "monthly")
    .map((report) => ({ ...report, safeUrl: safeExternalUrl(report.sourceUrl) }))
    .filter((report) => report.safeUrl);
  const currentDeals = (gym.deals ?? [])
    .map((deal) => ({ ...deal, safeUrl: safeExternalUrl(deal.sourceUrl) }))
    .filter((deal) => deal.safeUrl);
  const mandatoryFees = [
    ["Annual fee", gym.annualFee],
    ["Enrollment fee", gym.enrollmentFee],
    ["Initiation fee", gym.initiationFee],
    ["Processing fee", gym.processingFee],
    ["Activation fee", gym.activationFee],
  ].filter((entry): entry is [string, number] => typeof entry[1] === "number");

  return (
    <main className="shell detail-page">
      <header className="topbar">
        <a className="brand brand-link" href={`${basePath}/`} aria-label="Back to SF Gyms home">
          <span className="logo" aria-hidden="true">S</span>
          <span><strong>SF Gyms</strong><small>A softer way to find your next gym.</small></span>
        </a>
        <a className="secondary back-link" href={`${basePath}/`}>Back to map</a>
      </header>

      <div className="detail-page-wrap">
        <nav className="breadcrumb" aria-label="Breadcrumb"><a href={`${basePath}/`}>San Francisco gyms</a><span aria-hidden="true">/</span><span>{gym.name}</span></nav>

        <section className="detail-page-hero">
          <div>
            <div className="eyebrow">{gym.neighborhood} / {venueTypeLabels[gym.venueType]}{gym.recordStatus === "coming_soon" ? " / Coming soon" : ""}</div>
            <h1>{gym.name}</h1>
            <p className="detail-page-address">{gym.address}</p>
            <p className="detail-page-intro">{gym.description}</p>
          </div>
          <GymDetailActions gymId={gym.id} gymName={gym.name} />
        </section>

        <div className="detail-page-grid">
          <section className="detail-page-card detail-page-prices">
            <div className="detail-card-heading"><div><div className="eyebrow">Cost snapshot</div><h2>Prices</h2></div><span className={`cost-status cost-status-${gym.pricingStatus ?? "unresolved"}`}>{pricingStatusText(gym)}</span></div>
            <div className="detail-price-grid">
              <div>
                <span>{gym.operatorConfirmedMonthly ? "Operator-confirmed monthly" : gym.reportedMonthly ? "Recently reported monthly" : gym.estimatedMonthly ? "Typical monthly estimate" : "Monthly membership"}</span>
                <strong>{monthlyCostText(gym)}</strong>
                {gym.monthlyPrice === null && gym.monthlyPriceBlocker && <small>{gym.monthlyPriceBlocker}</small>}
              </div>
              <div>
                <span>{selectedDropIn?.name ?? "Day pass"}</span>
                <strong>{priceText(gym.dayPassPrice, "")}</strong>
                {selectedDropIn && gym.dayPassPrice !== null && <small>{selectedDropIn.accessScope}</small>}
                {gym.dayPassPrice === null && gym.dayPassPriceBlocker && <small>{gym.dayPassPriceBlocker}</small>}
              </div>
            </div>
            {gym.estimatedMonthly && <div className="estimate-explainer">
              <strong>Likely range: ${gym.estimatedMonthly.low.toFixed(0)}–${gym.estimatedMonthly.high.toFixed(0)}/month</strong>
              <span>{gym.estimatedMonthly.confidence} confidence · {gym.estimatedMonthly.basis} · {gym.estimatedMonthly.sampleSize} comparable prices</span>
              <span>Calculated {gym.estimatedMonthly.generatedAt}. This is not a quoted price from the gym.</span>
            </div>}
            {gym.operatorConfirmedMonthly && <div className="estimate-explainer reported-explainer">
              <strong>{gym.operatorConfirmedMonthly.planName || "Standard recurring plan"}: ${gym.operatorConfirmedMonthly.normalizedMonthly.toFixed(2)}/month</strong>
              <span>Confirmed privately by the operator on {gym.operatorConfirmedMonthly.confirmedAt} via {gym.operatorConfirmedMonthly.contactMethod}.</span>
              <span>This exact amount is not publicly reproducible and is excluded from official-price sorting.</span>
            </div>}
            {gym.reportedMonthly && <div className="estimate-explainer reported-explainer">
              <strong>Reported range: ${gym.reportedMonthly.low.toFixed(0)}–${gym.reportedMonthly.high.toFixed(0)}/month</strong>
              <span>{gym.reportedMonthly.confidence} confidence · {gym.reportedMonthly.sourceCount} independent recent sources · newest {gym.reportedMonthly.newestPublishedAt}</span>
              <span>{gym.reportedMonthly.conflict ? "Recent reports disagree materially; confirm the current plan with the gym." : "The reports are within 20% of one another."} These are not operator-verified prices.</span>
              {reportedSources.length > 0 && <span>{reportedSources.map((report, index) => <span key={report.id}>{index > 0 ? " · " : ""}<a href={report.safeUrl} target="_blank" rel="noreferrer">Source {index + 1}</a></span>)}</span>}
            </div>}
            {(gym.costContext ?? []).length > 0 && <div className="estimate-explainer reported-explainer">
              <strong>Officially published cost context</strong>
              {(gym.costContext ?? []).map((context) => <span key={context.id}>{context.label || context.productType}: {context.low === context.high ? `from $${context.low.toFixed(2)}` : `$${context.low.toFixed(2)}–$${context.high.toFixed(2)}`}{context.cadence !== "unknown" ? ` per ${context.cadence}` : ""}</span>)}
              <span>Ranges and starting prices are informative only and are not treated as exact selectable plans.</span>
            </div>}
            {selectedPlan && <p className="detail-muted">
              <strong>{selectedPlan.name}</strong>{selectedPlan.accessScope ? ` — ${selectedPlan.accessScope}` : ""}
              {selectedPlan.classAllowance?.disclosed && <>{" · "}{selectedPlan.classAllowance.unlimited ? "Unlimited classes" : `${selectedPlan.classAllowance.count} classes per ${selectedPlan.classAllowance.period}`}</>}
              {selectedPlan.billing.amount !== null && selectedPlan.billing.interval !== "month" && <>{" · "}${selectedPlan.billing.amount.toFixed(2)} every {selectedPlan.billing.interval}</>}
            </p>}
            {(typicalPlan || highestAccessPlan) && <div className="detail-price-grid">
              {typicalPlan && <div><span>Typical eligible plan</span><strong>{typicalPlan.billing.normalizedMonthly === null ? "Not listed" : `$${typicalPlan.billing.normalizedMonthly.toFixed(2)}/mo`}</strong><small>{typicalPlan.name}</small></div>}
              {highestAccessPlan && <div><span>Highest-access eligible plan</span><strong>{highestAccessPlan.billing.normalizedMonthly === null ? "Not listed" : `$${highestAccessPlan.billing.normalizedMonthly.toFixed(2)}/mo`}</strong><small>{highestAccessPlan.name}</small></div>}
            </div>}
            {bestValuePlan && <div className="estimate-explainer"><strong>Operator-labeled best value</strong><span>{bestValuePlan.name}{bestValuePlan.billing.normalizedMonthly === null ? "" : ` · $${bestValuePlan.billing.normalizedMonthly.toFixed(2)}/month`}</span></div>}
            {currentDeals.length > 0 && <div className="estimate-explainer">
              <strong>Current official deals</strong>
              {currentDeals.map((deal) => <span key={deal.id}><a href={deal.safeUrl} target="_blank" rel="noreferrer">{deal.label || `${deal.productType} offer`}</a>{` · $${deal.amount.toFixed(2)}${deal.cadence ? ` ${deal.cadence}` : ""}`}{deal.expiresAt ? ` · expires ${deal.expiresAt}` : ` · checked ${deal.capturedAt}`}</span>)}
              <span>Promotions are shown separately and never replace the ordinary plan price.</span>
            </div>}
            {gym.catalogStatus?.plans.status === "selected-only" && <p className="detail-muted">The selected official plan is verified; alternative membership products have not yet been fully reconstructed.</p>}
            {gym.catalogStatus?.plans.status === "source-fragment" && <p className="detail-muted">The displayed official offers were reviewed, but the operator may publish additional membership products that the source did not expose to the crawler.</p>}
            {mandatoryFees.length > 0 && <div className="detail-price-grid">{mandatoryFees.map(([label, amount]) => <div key={label}><span>{label}</span><strong>{priceText(amount, "")}</strong></div>)}</div>}
            <p className="detail-muted">{priceFreshnessText(gym)}</p>
            {gym.priceNote && <p className="price-note">{gym.priceNote}</p>}
            {gym.annualFeeNote && <p className="price-note">Annual fee details: {gym.annualFeeNote}</p>}
            {gym.pricingBlocker && <p className="price-note">{gym.pricingBlocker}</p>}
            {priceSourceUrl && <a className="detail-source-link" href={priceSourceUrl} target="_blank" rel="noreferrer">Read the official price source</a>}
          </section>

          <section className="detail-page-card">
            <div className="eyebrow">What is here</div>
            <h2>Amenities</h2>
            {gym.amenities.length > 0 ? <div className="detail-amenities">{gym.amenities.map((amenity) => <span className="price-pill" key={amenity}>{amenity}</span>)}</div> : <p className="detail-muted">{gym.metadataStatus?.amenities.reason || "Amenities have not been listed yet."}</p>}
          </section>

          <section className="detail-page-card">
            <div className="eyebrow">Plan your visit</div>
            <h2>Hours & location</h2>
            <dl className="detail-facts">
              <div><dt>Hours</dt><dd>{gym.hours === "Hours not listed" ? (gym.metadataStatus?.hours.reason || "Not published") : gym.hours}</dd></div>
              <div><dt>Address</dt><dd>{gym.address}</dd></div>
              <div><dt>Open 24/7</dt><dd>{gym.isOpen247 ? "Yes" : "Not listed"}</dd></div>
            </dl>
            <p className="detail-muted">Hours and access details can change. Confirm with the gym before visiting.</p>
          </section>

          <GymExperienceReports gymLocationId={reviewLocationId(gym)} gymName={gym.name} />

          <section className="detail-page-card detail-page-about">
            <div className="eyebrow">About this listing</div>
            <h2>Source and next steps</h2>
            <p className="detail-muted">This listing is assembled from committed directory data and source links. Pricing, hours, amenities, and availability are informational and should be confirmed directly with the gym.</p>
            <div className="detail-actions">
              {websiteUrl && <a className="primary" href={websiteUrl} target="_blank" rel="noreferrer">Visit gym site</a>}
              {listingUrl && <a className="secondary" href={listingUrl} target="_blank" rel="noreferrer">View {gym.sourceName} listing</a>}
            </div>
            <p className="source-note">Listing source: {gym.sourceName}. Imported {gym.importedAt ? new Date(gym.importedAt).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" }) : "date not listed"}.</p>
          </section>
        </div>
      </div>
    </main>
  );
}
