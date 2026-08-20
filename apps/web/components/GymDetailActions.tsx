"use client";

import { useEffect, useState } from "react";

import { readCompareIds, writeCompareIds } from "../lib/compare-state";
import { demoGyms } from "../lib/demo-data";
import CompareTray from "./CompareTray";

type GymDetailActionsProps = {
  gymId: string;
  gymName: string;
};

function readIds(key: string): string[] {
  try {
    const value = JSON.parse(window.localStorage.getItem(key) ?? "[]") as unknown;
    return Array.isArray(value) && value.every((item) => typeof item === "string") ? value : [];
  } catch {
    return [];
  }
}

export default function GymDetailActions({ gymId, gymName }: GymDetailActionsProps) {
  const [saved, setSaved] = useState(false);
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [message, setMessage] = useState("");

  useEffect(() => {
    setSaved(readIds("sf-gyms:saved").includes(gymId));
    setCompareIds(readCompareIds(window.localStorage, new Set(demoGyms.map((gym) => gym.id))));
  }, [gymId]);

  const toggleSaved = () => {
    const ids = readIds("sf-gyms:saved");
    const next = ids.includes(gymId) ? ids.filter((id) => id !== gymId) : [...ids, gymId];
    window.localStorage.setItem("sf-gyms:saved", JSON.stringify(next));
    setSaved(next.includes(gymId));
    setMessage(next.includes(gymId) ? `${gymName} saved on this device.` : `${gymName} removed from saved gyms.`);
  };

  const toggleCompared = () => {
    const ids = compareIds;
    if (!ids.includes(gymId) && ids.length >= 3) {
      setMessage("Compare up to three gyms at a time.");
      return;
    }
    const next = ids.includes(gymId) ? ids.filter((id) => id !== gymId) : [...ids, gymId];
    writeCompareIds(window.localStorage, next);
    setCompareIds(next);
    setMessage(next.includes(gymId) ? `${gymName} added to compare.` : `${gymName} removed from compare.`);
  };

  const compared = compareIds.includes(gymId);

  const removeCompared = (id: string) => {
    const next = compareIds.filter((item) => item !== id);
    writeCompareIds(window.localStorage, next);
    setCompareIds(next);
  };

  return (
    <>
      <div className="detail-page-actions">
        <button className="primary" type="button" onClick={toggleSaved} aria-pressed={saved}>
          {saved ? "Saved" : "Save gym"}
        </button>
        <button className="secondary" type="button" onClick={toggleCompared} aria-pressed={compared}>
          {compared ? "Remove from compare" : "Add to compare"}
        </button>
        {message && <span className="detail-action-message" role="status">{message}</span>}
      </div>
      <CompareTray gyms={demoGyms} compareIds={compareIds} onRemove={removeCompared} />
    </>
  );
}
