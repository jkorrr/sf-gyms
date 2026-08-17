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
const OSM_RASTER_STYLE = {
  version: 8 as const,
  sources: {
    osm: {
      type: "raster" as const,
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm-raster", type: "raster" as const, source: "osm" }],
};

type Basemap = "openfreemap" | "osm";

type GymMapProps = {
  gyms: Gym[];
  selectedId?: string;
  origin: GeoPoint | null;
  onSelect: (gym: Gym | null) => void;
};

export default function GymMap({ gyms, selectedId, origin, onSelect }: GymMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const gymsRef = useRef(gyms);
  const markersRef = useRef<globalThis.Map<string, maplibregl.Marker>>(new globalThis.Map());
  const originMarkerRef = useRef<maplibregl.Marker | null>(null);
  const onSelectRef = useRef(onSelect);
  const fallbackTimerRef = useRef<number | null>(null);
  const basemapRef = useRef<Basemap>("osm");
  const [isReady, setIsReady] = useState(false);
  const [basemap, setBasemap] = useState<Basemap>("osm");
  const [mapError, setMapError] = useState("");

  useEffect(() => {
    gymsRef.current = gyms;
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
    map.once("load", () => map.resize());
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
      markersRef.current.forEach((marker) => marker.remove());
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

    markersRef.current.forEach((marker) => marker.remove());
    markersRef.current.clear();
    gyms.forEach((gym) => {
      const element = document.createElement("button");
      element.type = "button";
      element.className = "gym-marker";
      element.setAttribute("aria-label", `Open ${gym.name}`);
      element.title = gym.name;
      element.addEventListener("click", (event) => {
        event.stopPropagation();
        onSelectRef.current(gym);
        map.flyTo({ center: [gym.longitude, gym.latitude], zoom: Math.max(map.getZoom(), 14), duration: 500, essential: true });
      });
      const marker = new maplibregl.Marker({ element, anchor: "center" })
        .setLngLat([gym.longitude, gym.latitude])
        .addTo(map);
      markersRef.current.set(gym.id, marker);
    });

    return () => {
      markersRef.current.forEach((marker) => marker.remove());
      markersRef.current.clear();
    };
  }, [gyms, isReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isReady || gyms.length === 0) return;
    const bounds = new maplibregl.LngLatBounds();
    gyms.forEach((gym) => bounds.extend([gym.longitude, gym.latitude]));
    map.fitBounds(bounds, { padding: { top: 170, right: 44, bottom: 250, left: 44 }, maxZoom: 14, duration: 500 });
  }, [gyms, isReady]);

  useEffect(() => {
    markersRef.current.forEach((marker, id) => marker.getElement().classList.toggle("active", id === selectedId));
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

  return (
    <div className="map-shell">
      <div
        ref={containerRef}
        className="real-map"
        aria-label="Interactive map of San Francisco gyms"
        role="application"
        style={{ touchAction: "none" }}
      />
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
      {gyms.length === 0 && <div className="map-empty" style={{ pointerEvents: "none" }}>No gyms match those filters. Try another neighborhood.</div>}
    </div>
  );
}
