"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import type { Gym } from "../lib/demo-data";
import type { GeoPoint } from "../lib/geo";

const OPENFREEMAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";
const SF_CENTER: [number, number] = [-122.4194, 37.7749];
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
  const [isReady, setIsReady] = useState(false);
  const [basemap, setBasemap] = useState<Basemap>("openfreemap");
  const [mapError, setMapError] = useState("");

  useEffect(() => {
    gymsRef.current = gyms;
  }, [gyms]);

  useEffect(() => {
    onSelectRef.current = onSelect;
  }, [onSelect]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: OPENFREEMAP_STYLE,
      center: SF_CENTER,
      zoom: 12.1,
      attributionControl: { compact: true },
      cooperativeGestures: true,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.FullscreenControl(), "top-right");
    map.once("load", () => map.resize());
    map.on("click", () => onSelectRef.current(null));
    map.on("error", (event) => {
      if (event.error?.message) setMapError(event.error.message);
    });
    mapRef.current = map;
    setIsReady(true);

    const fallbackToOsm = () => {
      if (mapRef.current !== map || basemap !== "openfreemap") return;
      setMapError("OpenFreeMap tiles did not finish loading, so the map switched to OpenStreetMap streets.");
      setBasemap("osm");
      map.setStyle(OSM_RASTER_STYLE);
    };
    fallbackTimerRef.current = window.setTimeout(fallbackToOsm, 3500);
    map.on("sourcedata", (event) => {
      if (event.sourceId === "openmaptiles" && event.isSourceLoaded && fallbackTimerRef.current !== null) {
        window.clearTimeout(fallbackTimerRef.current);
        fallbackTimerRef.current = null;
      }
    });

    return () => {
      if (fallbackTimerRef.current !== null) window.clearTimeout(fallbackTimerRef.current);
      originMarkerRef.current?.remove();
      markersRef.current.forEach((marker) => marker.remove());
      markersRef.current.clear();
      map.remove();
      mapRef.current = null;
      setIsReady(false);
    };
    // The initial map must only be constructed once. Basemap switches use setStyle below.
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
    if (fallbackTimerRef.current !== null) {
      window.clearTimeout(fallbackTimerRef.current);
      fallbackTimerRef.current = null;
    }
    const next: Basemap = basemap === "openfreemap" ? "osm" : "openfreemap";
    setBasemap(next);
    setMapError("");
    map.setStyle(next === "openfreemap" ? OPENFREEMAP_STYLE : OSM_RASTER_STYLE);
    map.once("style.load", () => map.resize());
  };

  return (
    <div className="map-shell">
      <div ref={containerRef} className="real-map" aria-label="Interactive map of San Francisco gyms" />
      <div className="map-provider-control">
        <span>{basemap === "openfreemap" ? "OpenFreeMap vector" : "OpenStreetMap streets"}</span>
        <button type="button" onClick={switchBasemap}>{basemap === "openfreemap" ? "Use OSM streets" : "Try OpenFreeMap"}</button>
      </div>
      <div className="map-help" aria-hidden="true">Drag to explore · scroll to zoom · tap a dot for details</div>
      {mapError && <div className="map-status" role="status">{mapError}</div>}
      {gyms.length === 0 && <div className="map-empty">No gyms match those filters. Try another neighborhood.</div>}
    </div>
  );
}
