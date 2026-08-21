"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";

import { type Gym, venueTypeLabels } from "../lib/demo-data";
import { searchGymsForComparison } from "../lib/gym-search";

type GymComboboxProps = {
  slotIndex: number;
  gyms: Gym[];
  currentGym?: Gym;
  excludedIds: Set<string>;
  isOpen: boolean;
  onOpen: () => void;
  onClose: () => void;
  onSelect: (slotIndex: number, gymId: string) => void;
  onRemove: (slotIndex: number) => void;
};

export default function GymCombobox({
  slotIndex,
  gyms,
  currentGym,
  excludedIds,
  isOpen,
  onOpen,
  onClose,
  onSelect,
  onRemove,
}: GymComboboxProps) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(-1);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const generatedId = useId().replace(/:/g, "");
  const listboxId = `gym-options-${generatedId}`;
  const results = useMemo(() => searchGymsForComparison(gyms, query, excludedIds), [excludedIds, gyms, query]);

  useEffect(() => {
    if (!isOpen) {
      setActiveIndex(-1);
      return;
    }
    setActiveIndex(results.length > 0 ? 0 : -1);
  }, [isOpen, results.length]);

  useEffect(() => {
    if (!isOpen) return;
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) onClose();
    };
    document.addEventListener("pointerdown", closeOnOutsideClick);
    return () => document.removeEventListener("pointerdown", closeOnOutsideClick);
  }, [isOpen, onClose]);

  useEffect(() => {
    if (!isOpen || activeIndex < 0) return;
    document.getElementById(`${listboxId}-option-${activeIndex}`)?.scrollIntoView({ block: "nearest" });
  }, [activeIndex, isOpen, listboxId]);

  const openAndFocus = () => {
    setQuery("");
    onOpen();
    window.requestAnimationFrame(() => inputRef.current?.focus());
  };

  const choose = (gym: Gym) => {
    onSelect(slotIndex, gym.id);
    setQuery("");
    setActiveIndex(-1);
    onClose();
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      if (!isOpen) onOpen();
      setActiveIndex((current) => results.length === 0 ? -1 : Math.min(results.length - 1, current + 1));
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      if (!isOpen) onOpen();
      setActiveIndex((current) => results.length === 0 ? -1 : current <= 0 ? results.length - 1 : current - 1);
      return;
    }
    if (event.key === "Enter" && isOpen && activeIndex >= 0 && results[activeIndex]) {
      event.preventDefault();
      choose(results[activeIndex]);
    }
  };

  if (currentGym && !isOpen) {
    return (
      <article className="gym-combobox-card">
        <span className="gym-combobox-slot">Gym {slotIndex + 1}</span>
        <strong>{currentGym.name}</strong>
        <span>{currentGym.neighborhood} · {venueTypeLabels[currentGym.venueType]}</span>
        <small>{currentGym.address}</small>
        <div className="gym-combobox-card-actions">
          <button type="button" onClick={openAndFocus}>Change</button>
          <button type="button" onClick={() => onRemove(slotIndex)}>Remove</button>
        </div>
      </article>
    );
  }

  return (
    <div className="gym-combobox" ref={rootRef}>
      <label htmlFor={`gym-picker-${generatedId}`}>{currentGym ? `Change gym ${slotIndex + 1}` : `Add gym ${slotIndex + 1}`}</label>
      <div className="gym-combobox-input-wrap">
        <input
          ref={inputRef}
          id={`gym-picker-${generatedId}`}
          role="combobox"
          aria-autocomplete="list"
          aria-expanded={isOpen}
          aria-controls={listboxId}
          aria-activedescendant={isOpen && activeIndex >= 0 ? `${listboxId}-option-${activeIndex}` : undefined}
          value={query}
          onFocus={onOpen}
          onClick={onOpen}
          onChange={(event) => {
            setQuery(event.target.value);
            setActiveIndex(0);
            onOpen();
          }}
          onKeyDown={handleKeyDown}
          placeholder="Type a gym, area, address, or venue type"
        />
        {currentGym && <button className="gym-combobox-cancel" type="button" onClick={onClose}>Cancel</button>}
      </div>
      {isOpen && <div className="gym-combobox-options" id={listboxId} role="listbox" aria-label={`Gym ${slotIndex + 1} options`}>
        {results.length === 0 ? <p>No matching gyms. Try a broader name or neighborhood.</p> : results.map((gym, index) => (
          <div
            id={`${listboxId}-option-${index}`}
            className={`gym-combobox-option${index === activeIndex ? " active" : ""}`}
            role="option"
            aria-selected={index === activeIndex}
            aria-posinset={index + 1}
            aria-setsize={results.length}
            key={gym.id}
            onMouseEnter={() => setActiveIndex(index)}
            onMouseDown={(event) => {
              event.preventDefault();
              choose(gym);
            }}
          >
            <strong>{gym.name}</strong>
            <span>{gym.neighborhood} · {venueTypeLabels[gym.venueType]}</span>
            <small>{gym.address} · {gym.gymType}</small>
          </div>
        ))}
      </div>}
      <span className="sr-only" role="status" aria-live="polite">
        {isOpen ? `${results.length} matching gyms. ${activeIndex >= 0 && results[activeIndex] ? `${results[activeIndex].name}, ${results[activeIndex].neighborhood}` : ""}` : ""}
      </span>
    </div>
  );
}
