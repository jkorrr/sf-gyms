"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import type { Gym } from "../lib/demo-data";
import type { GeoPoint } from "../lib/geo";

const OPENFREEMAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";
const SF_CENTER: [number, number] = [-122.4194, 37.7749];
// OSM is the reliable visual baseline. OpenFreeMap remains available as a
// vector style, but a slow or blocked vector source must never leave a blank map.
type Basemap = "openfreemap" | "osm";

type GymMapProps = {
  gyms: Gym[];
  selectedId?: string;
  origin: GeoPoint | null;
  onSelect: (gym: Gym | null) => void;
};

const OSM_RASTER_STYLE: maplibregl.StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm-raster", type: "raster", source: "osm" }],
};

type GymMarker = {
  element: HTMLButtonElement;
  marker: maplibregl.Marker;
};

function fitMapToGyms(map: maplibregl.Map, gyms: Gym[], duration = 500) {
  if (gyms.length === 0) {
    map.easeTo({ center: SF_CENTER, zoom: 12.1, duration, essential: true });
    return;
  }

  const bounds = new maplibregl.LngLatBounds();
  gyms.forEach((gym) => bounds.extend([gym.longitude, gym.latitude]));
  const compactPadding = Math.max(28, Math.min(60, Math.round(map.getContainer().clientHeight * 0.08)));
  map.fitBounds(bounds, { padding: compactPadding, maxZoom: 14, duration, essential: true });
}

export default function GymMap({ gyms, selectedId, origin, onSelect }: GymMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const gymsRef = useRef(gyms);
  const gymsByIdRef = useRef(new globalThis.Map(gyms.map((gym) => [gym.id, gym])));
  const markersRef = useRef<globalThis.Map<string, GymMarker>>(new globalThis.Map());
  const originMarkerRef = useRef<maplibregl.Marker | null>(null);
  const onSelectRef = useRef(onSelect);
  const fallbackTimerRef = useRef<number | null>(null);
  const basemapRef = useRef<Basemap>("osm");
  const [isReady, setIsReady] = useState(false);
  const [basemap, setBasemap] = useState<Basemap>("osm");
  const [mapError, setMapError] = useState("");

  useEffect(() => {
    gymsRef.current = gyms;
    gymsByIdRef.current = new globalThis.Map(gyms.map((gym) => [gym.id, gym]));
  }, [gyms]);

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  const clearFallbackTimer = () => {
    if (fallbackTimerRef.current !== null) {
      window.clearTimeout(fallbackTimerRef.current);
      fallbackTimerRef.current = null;
    }
  };

  const fallbackToOsm = () => {
    const map = mapRef.current;
    if (!map || basemapRef.current !== "openfreemap") return;
    clearFallbackTimer();
    basemapRef.current = "osm";
    setBasemap("osm");
    setMapError("OpenFreeMap did not finish loading, so the map is using OpenStreetMap streets.");
    map.setStyle(OSM_RASTER_STYLE);
    map.once("style.load", () => map.resize());
  };

  const watchOpenFreeMap = () => {
    clearFallbackTimer();
    fallbackTimerRef.current = window.setTimeout(fallbackToOsm, 1800);
  };

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: OSM_RASTER_STYLE,
      center: SF_CENTER,
      zoom: 12.1,
      attributionControl: { compact: true },
    });

    // Keep interaction local to the map widget. Cooperative gestures make a
    // normal wheel gesture require Ctrl/Command, which is surprising here and
    // makes the map feel like a static image. Explicitly enabling the handlers
    // also documents the interaction contract for future style/provider swaps.
    map.scrollZoom.enable();
    map.dragPan.enable();
    map.touchZoomRotate.enable();
    map.doubleClickZoom.enable();
    map.keyboard.enable();
    map.getCanvas().style.touchAction = "none";

    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.FullscreenControl(), "top-right");
    map.on("load", () => map.resize());
    map.on("click", () => onSelectRef.current(null));
    map.on("error", (event) => {
      if (basemapRef.current === "osm" && event.error?.message) setMapError("Map tiles could not load. Try the OpenFreeMap vector option.");
    });
    map.on("sourcedata", (event) => {
      if (basemapRef.current === "openfreemap" && event.sourceId === "openmaptiles" && event.isSourceLoaded) clearFallbackTimer();
    });
    mapRef.current = map;
    setIsReady(true);

    return () => {
      clearFallbackTimer();
      originMarkerRef.current?.remove();
      markersRef.current.forEach(({ marker }) => marker.remove());
      markersRef.current.clear();
      map.remove();
      mapRef.current = null;
      setIsReady(false);
    };
    // The map is constructed once. Basemap switches use setStyle below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isReady) return;

    const visibleIds = new Set(gyms.map((gym) => gym.id));
    markersRef.current.forEach(({ marker }, id) => {
      if (!visibleIds.has(id)) {
        marker.remove();
        markersRef.current.delete(id);
      }
    });

    gyms.forEach((gym) => {
      const existing = markersRef.current.get(gym.id);
      if (existing) {
        existing.marker.setLngLat([gym.longitude, gym.latitude]);
        existing.element.classList.toggle("active", gym.id === selectedId);
        return;
      }

      const element = document.createElement("button");
      element.type = "button";
      element.className = "gym-marker";
      element.setAttribute("aria-label", `Open ${gym.name}`);
      element.title = gym.name;
      element.classList.toggle("active", gym.id === selectedId);
      element.addEventListener("click", (event) => {
        event.stopPropagation();
        const currentGym = gymsByIdRef.current.get(gym.id);
        if (!currentGym) return;
        onSelectRef.current(currentGym);
        map.flyTo({
          center: [currentGym.longitude, currentGym.latitude],
          zoom: Math.max(map.getZoom(), 14),
          duration: 500,
          essential: true,
        });
      });
      const marker = new maplibregl.Marker({ element, anchor: "center" })
        .setLngLat([gym.longitude, gym.latitude])
        .addTo(map);
      markersRef.current.set(gym.id, { element, marker });
    });
  }, [gyms, isReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isReady || gyms.length === 0) return;
    fitMapToGyms(map, gyms);
  }, [gyms, isReady]);

  useEffect(() => {
    if (!isReady) return;
    markersRef.current.forEach(({ element }, id) => element.classList.toggle("active", id === selectedId));
  }, [selectedId, isReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isReady) return;
    originMarkerRef.current?.remove();
    originMarkerRef.current = null;
    if (!origin) return;
    const element = document.createElement("div");
    element.className = "origin-marker";
    element.setAttribute("aria-label", origin.label);
    element.title = origin.label;
    originMarkerRef.current = new maplibregl.Marker({ element }).setLngLat([origin.longitude, origin.latitude]).addTo(map);
    map.flyTo({ center: [origin.longitude, origin.latitude], zoom: Math.max(map.getZoom(), 13), duration: 500 });
  }, [origin, isReady]);

  const switchBasemap = () => {
    const map = mapRef.current;
    if (!map) return;
    clearFallbackTimer();
    const next: Basemap = basemapRef.current === "openfreemap" ? "osm" : "openfreemap";
    basemapRef.current = next;
    setBasemap(next);
    setMapError("");
    map.setStyle(next === "openfreemap" ? OPENFREEMAP_STYLE : OSM_RASTER_STYLE);
    map.once("style.load", () => map.resize());
    if (next === "openfreemap") watchOpenFreeMap();
  };

  const recenterMap = () => {
    const map = mapRef.current;
    if (!map) return;
    onSelectRef.current(null);
    fitMapToGyms(map, gymsRef.current, 650);
  };

  return (
    <div className="map-shell">
      <div
        ref={containerRef}
        className="real-map"
        aria-label="Interactive map of San Francisco gyms"
        role="application"
        style={{ touchAction: "none" }}
      />
      <button
        type="button"
        className="map-recenter-control"
        onClick={recenterMap}
        aria-label="Re-center map"
        title="Re-center map"
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="12" cy="12" r="5" />
          <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
        </svg>
      </button>
      <div className="map-provider-control" style={{ pointerEvents: "none" }}>
        <span>{basemap === "openfreemap" ? "OpenFreeMap vector" : "OpenStreetMap streets"}</span>
        <button
          type="button"
          onClick={switchBasemap}
          style={{ pointerEvents: "auto" }}
        >
          {basemap === "openfreemap" ? "Use OSM streets" : "Try OpenFreeMap"}
        </button>
      </div>
      <div className="map-help" aria-hidden="true">Drag to explore - scroll to zoom - tap a dot for details</div>
      {mapError && <div className="map-status" role="status" style={{ pointerEvents: "none" }}>{mapError}</div>}
      {gyms.length === 0 && <div className="map-empty" style={{ pointerEvents: "none" }}>No venues match those filters. Try another type or neighborhood.</div>}
    </div>
  );
}
