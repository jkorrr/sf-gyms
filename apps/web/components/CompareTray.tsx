"use client";

import { basePath } from "../lib/config";
import type { Gym } from "../lib/demo-data";

type CompareTrayProps = {
  gyms: Gym[];
  compareIds: string[];
  onRemove: (id: string) => void;
};

export default function CompareTray({ gyms, compareIds, onRemove }: CompareTrayProps) {
  if (compareIds.length === 0) return null;
  const selected = compareIds.map((id) => gyms.find((gym) => gym.id === id)).filter((gym): gym is Gym => Boolean(gym));
  const params = new URLSearchParams({ gyms: compareIds.join(",") });

  return (
    <aside className="compare-tray" aria-label="Gym comparison tray">
      <div className="compare-tray-title"><strong>Compare</strong><span>{compareIds.length}/3 gyms</span></div>
      <div className="compare-tray-slots">
        {selected.map((gym) => <span className="compare-tray-chip" key={gym.id}>
          <span>{gym.name}</span>
          <button type="button" onClick={() => onRemove(gym.id)} aria-label={`Remove ${gym.name} from comparison`}>×</button>
        </span>)}
        {Array.from({ length: Math.max(0, 3 - selected.length) }, (_, index) => <span className="compare-tray-empty" key={index}>Add gym</span>)}
      </div>
      <a className="compare-tray-action" href={`${basePath}/compare/?${params.toString()}`}>Compare value <span aria-hidden="true">→</span></a>
    </aside>
  );
}

