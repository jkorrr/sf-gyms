"use client";

import { useEffect, useState } from "react";

import {
  experienceContext,
  experienceSignalLabels,
  type ExperienceReport,
  type ExperienceReportPage,
} from "../lib/experience-reports";

type GymExperienceReportsProps = {
  gymLocationId?: string;
  gymName: string;
};

type LoadState = "loading" | "ready" | "error";

export default function GymExperienceReports({ gymLocationId, gymName }: GymExperienceReportsProps) {
  const [reports, setReports] = useState<ExperienceReport[]>([]);
  const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");
  const [state, setState] = useState<LoadState>(gymLocationId && apiBase ? "loading" : "ready");

  useEffect(() => {
    if (!gymLocationId || !apiBase) {
      setState("ready");
      return;
    }
    const controller = new AbortController();
    setState("loading");
    void fetch(`${apiBase}/api/v1/gyms/${encodeURIComponent(gymLocationId)}/experience-reports?limit=10`, {
      headers: { Accept: "application/json" },
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`Experience reports request failed with ${response.status}`);
        return response.json() as Promise<ExperienceReportPage>;
      })
      .then((page) => {
        setReports(page.items);
        setState("ready");
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        setState("error");
      });
    return () => controller.abort();
  }, [apiBase, gymLocationId]);

  return (
    <section className="detail-page-card detail-page-experiences" aria-labelledby="gym-experiences-title">
      <div className="detail-card-heading">
        <div>
          <div className="eyebrow">Firsthand, time-stamped context</div>
          <h2 id="gym-experiences-title">Recent gym experiences</h2>
        </div>
        <span className="experience-count">{reports.length > 0 ? `${reports.length} published` : "Moderated"}</span>
      </div>

      {state === "loading" && <p className="experience-state" role="status">Loading published experiences…</p>}
      {state === "error" && <p className="experience-state experience-error" role="status">Published experiences could not be loaded. Try again later.</p>}
      {state === "ready" && reports.length === 0 && <div className="experience-empty">
        <strong>No published member observations yet</strong>
        <p>
          {gymLocationId
            ? `${gymName} has not received a moderated experience report yet.`
            : "This directory listing is not yet linked to the moderated review system."}
        </p>
        <small>Ratings are not inferred from other websites, and unmoderated submissions never appear here.</small>
      </div>}

      {reports.length > 0 && <div className="experience-list">
        {reports.map((report) => {
          const signals = experienceSignalLabels(report);
          return <article className="experience-card" key={report.id}>
            <p className="experience-context">{experienceContext(report)}</p>
            {signals.length > 0 && <ul className="experience-signals" aria-label="Reported observations">
              {signals.map((signal) => <li key={signal}>{signal}</li>)}
            </ul>}
            {report.body && <p className="experience-body">{report.body}</p>}
            <p className="experience-disclosure">Self-reported experience · identity and attendance not independently verified</p>
          </article>;
        })}
      </div>}
    </section>
  );
}
