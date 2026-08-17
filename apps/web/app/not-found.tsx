import { basePath } from "../lib/config";

export default function NotFound() {
  return (
    <main className="shell not-found-page">
      <div className="not-found-card">
        <div className="logo" aria-hidden="true">S</div>
        <div className="eyebrow">Listing not found</div>
        <h1>That gym is not in the directory.</h1>
        <p>It may have moved, been removed, or the link may be incomplete. Return to the San Francisco map to choose another listing.</p>
        <a className="primary" href={`${basePath}/`}>Back to SF Gyms</a>
      </div>
    </main>
  );
}
