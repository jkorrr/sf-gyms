import GymDetailActions from "../../../components/GymDetailActions";
import { basePath } from "../../../lib/config";
import { demoGyms } from "../../../lib/demo-data";
import { getGymById, priceFreshnessText, priceText, safeExternalUrl } from "../../../lib/gym-detail";
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
            <div className="eyebrow">{gym.neighborhood} / {gym.gymType}</div>
            <h1>{gym.name}</h1>
            <p className="detail-page-address">{gym.address}</p>
            <p className="detail-page-intro">{gym.description}</p>
          </div>
          <GymDetailActions gymId={gym.id} gymName={gym.name} />
        </section>

        <div className="detail-page-grid">
          <section className="detail-page-card detail-page-prices">
            <div className="detail-card-heading"><div><div className="eyebrow">Cost snapshot</div><h2>Prices</h2></div><span className={`freshness ${gym.priceSource ? "" : "stale"}`}>{gym.priceSource ? "Source checked" : "Verify first"}</span></div>
            <div className="detail-price-grid">
              <div><span>Monthly membership</span><strong>{priceText(gym.monthlyPrice, "/mo")}</strong></div>
              <div><span>Day pass</span><strong>{priceText(gym.dayPassPrice, "")}</strong></div>
            </div>
            <p className="detail-muted">{priceFreshnessText(gym)}</p>
            {gym.priceNote && <p className="price-note">{gym.priceNote}</p>}
            {priceSourceUrl && <a className="detail-source-link" href={priceSourceUrl} target="_blank" rel="noreferrer">Read the official price source</a>}
          </section>

          <section className="detail-page-card">
            <div className="eyebrow">What is here</div>
            <h2>Amenities</h2>
            {gym.amenities.length > 0 ? <div className="detail-amenities">{gym.amenities.map((amenity) => <span className="price-pill" key={amenity}>{amenity}</span>)}</div> : <p className="detail-muted">Amenities have not been listed yet.</p>}
          </section>

          <section className="detail-page-card">
            <div className="eyebrow">Plan your visit</div>
            <h2>Hours & location</h2>
            <dl className="detail-facts">
              <div><dt>Hours</dt><dd>{gym.hours}</dd></div>
              <div><dt>Address</dt><dd>{gym.address}</dd></div>
              <div><dt>Open 24/7</dt><dd>{gym.isOpen247 ? "Yes" : "Not listed"}</dd></div>
            </dl>
            <p className="detail-muted">Hours and access details can change. Confirm with the gym before visiting.</p>
          </section>

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
