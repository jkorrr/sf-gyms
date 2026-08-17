"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import type { GeoPoint } from "../lib/geo";
import type { Gym } from "../lib/demo-data";

const OPENFREEMAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";
const SF_CENTER: [number, number] = [-122.4194, 37.7749];

type GymMapProps = {
  gyms: Gym[];
  selectedId?: string;
  origin: GeoPoint | null;
  onSelect: (gym: Gym | null) => void;
};

export default function GymMap({ gyms, selectedId, origin, onSelect }: GymMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const markersRef = useRef<globalThis.Map<string, maplibregl.Marker>>(new globalThis.Map());
  const originMarkerRef = useRef<maplibregl.Marker | null>(null);
  const onSelectRef = useRef(onSelect);
  const [isReady, setIsReady] = useState(false);

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
    // Mark the instance usable immediately so markers can mount even when a
    // vector-tile provider is slow. The first style load still gets a resize.
    map.once("load", () => map.resize());
    map.on("click", () => onSelectRef.current(null));
    mapRef.current = map;
    setIsReady(true);

    return () => {
      originMarkerRef.current?.remove();
      markersRef.current.forEach((marker) => marker.remove());
      markersRef.current.clear();
      map.remove();
      mapRef.current = null;
      setIsReady(false);
    };
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
        map.flyTo({
          center: [gym.longitude, gym.latitude],
          zoom: Math.max(map.getZoom(), 14),
          duration: 500,
          essential: true,
        });
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
    map.fitBounds(bounds, {
      padding: { top: 80, right: 44, bottom: 230, left: 44 },
      maxZoom: 14,
      duration: 500,
    });
  }, [gyms, isReady]);

  useEffect(() => {
    markersRef.current.forEach((marker, id) => {
      marker.getElement().classList.toggle("active", id === selectedId);
    });
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
    originMarkerRef.current = new maplibregl.Marker({ element })
      .setLngLat([origin.longitude, origin.latitude])
      .addTo(map);
    map.flyTo({ center: [origin.longitude, origin.latitude], zoom: Math.max(map.getZoom(), 13), duration: 500 });
  }, [origin, isReady]);

  return (
    <div className="map-shell">
      <div ref={containerRef} className="real-map" aria-label="Interactive map of San Francisco gyms" />
      <div className="map-help" aria-hidden="true">Drag to explore · scroll to zoom · tap a dot for details</div>
      {gyms.length === 0 && <div className="map-empty">No gyms in this map view. Try clearing a filter.</div>}
    </div>
  );
}
