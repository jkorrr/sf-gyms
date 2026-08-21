"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import { basePath } from "../lib/config";
import type { Gym } from "../lib/demo-data";
import type { GeoPoint } from "../lib/geo";
import { buildGymFeatureCollection } from "../lib/map-data";

const OPENFREEMAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";
const SF_CENTER: [number, number] = [-122.4194, 37.7749];
const GYM_SOURCE_ID = "gym-points";
const GYM_LAYER_ID = "gym-point-circles";
type Basemap = "openfreemap" | "osm";

type GymMapProps = {
  gyms: Gym[];
  selectedId?: string;
  highlightedId?: string | null;
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

function addGymSourceAndLayer(map: maplibregl.Map, gyms: Gym[]) {
  if (!map.getSource(GYM_SOURCE_ID)) {
    map.addSource(GYM_SOURCE_ID, {
      type: "geojson",
      data: buildGymFeatureCollection(gyms),
      promoteId: "id",
    });
  }

  if (!map.getLayer(GYM_LAYER_ID)) {
    map.addLayer({
      id: GYM_LAYER_ID,
      type: "circle",
      source: GYM_SOURCE_ID,
      paint: {
        "circle-radius": [
          "case",
          ["boolean", ["feature-state", "selected"], false], 12,
          ["boolean", ["feature-state", "highlighted"], false], 11,
          ["boolean", ["feature-state", "hover"], false], 11,
          9,
        ],
        "circle-color": [
          "case",
          ["boolean", ["feature-state", "selected"], false], "#27483c",
          ["boolean", ["feature-state", "highlighted"], false], "#806aa9",
          ["boolean", ["feature-state", "hover"], false], "#6b9479",
          "#75a789",
        ],
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": [
          "case",
          ["any",
            ["boolean", ["feature-state", "selected"], false],
            ["boolean", ["feature-state", "highlighted"], false],
            ["boolean", ["feature-state", "hover"], false],
          ], 3,
          2,
        ],
        "circle-opacity": 0.96,
      },
    });
  }
}

export default function GymMap({ gyms, selectedId, highlightedId, origin, onSelect }: GymMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const gymsRef = useRef(gyms);
  const gymsByIdRef = useRef(new globalThis.Map(gyms.map((gym) => [gym.id, gym])));
  const originMarkerRef = useRef<maplibregl.Marker | null>(null);
  const onSelectRef = useRef(onSelect);
  const selectedIdRef = useRef<string | undefined>(selectedId);
  const highlightedIdRef = useRef<string | null | undefined>(highlightedId);
  const hoveredIdRef = useRef<string | number | null>(null);
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

  const restoreFeatureStates = (map: maplibregl.Map) => {
    if (!map.getSource(GYM_SOURCE_ID)) return;
    const setState = (id: string | null | undefined, state: Record<string, boolean>) => {
      if (id && gymsByIdRef.current.has(id)) map.setFeatureState({ source: GYM_SOURCE_ID, id }, state);
    };
    setState(selectedIdRef.current, { selected: true });
    setState(highlightedIdRef.current, { highlighted: true });
    if (hoveredIdRef.current !== null) map.setFeatureState({ source: GYM_SOURCE_ID, id: hoveredIdRef.current }, { hover: true });
  };

  const fallbackToOsm = () => {
    const map = mapRef.current;
    if (!map || basemapRef.current !== "openfreemap") return;
    clearFallbackTimer();
    basemapRef.current = "osm";
    setBasemap("osm");
    setMapError("OpenFreeMap did not finish loading, so the map is using OpenStreetMap streets.");
    map.setStyle(OSM_RASTER_STYLE);
  };

  const watchOpenFreeMap = () => {
    clearFallbackTimer();
    fallbackTimerRef.current = window.setTimeout(fallbackToOsm, 1800);
  };

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    maplibregl.setWorkerUrl(`${basePath}/maplibre-gl-worker.mjs`);
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: OSM_RASTER_STYLE,
      center: SF_CENTER,
      zoom: 12.1,
      attributionControl: { compact: true },
    });

    map.scrollZoom.enable();
    map.dragPan.enable();
    map.touchZoomRotate.enable();
    map.doubleClickZoom.enable();
    map.keyboard.enable();
    map.getCanvas().style.touchAction = "none";
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    map.addControl(new maplibregl.FullscreenControl(), "top-right");

    const handleStyleLoad = () => {
      addGymSourceAndLayer(map, gymsRef.current);
      restoreFeatureStates(map);
      map.resize();
      setIsReady(true);
    };

    map.on("style.load", handleStyleLoad);
    map.on("load", handleStyleLoad);
    map.on("click", GYM_LAYER_ID, (event) => {
      const id = String(event.features?.[0]?.properties?.id ?? "");
      const gym = gymsByIdRef.current.get(id);
      if (gym) onSelectRef.current(gym);
    });
    map.on("click", (event) => {
      if (map.getLayer(GYM_LAYER_ID) && map.queryRenderedFeatures(event.point, { layers: [GYM_LAYER_ID] }).length > 0) return;
      onSelectRef.current(null);
    });
    map.on("mousemove", GYM_LAYER_ID, (event) => {
      map.getCanvas().style.cursor = "pointer";
      const nextId = event.features?.[0]?.id ?? null;
      if (hoveredIdRef.current === nextId) return;
      if (hoveredIdRef.current) map.setFeatureState({ source: GYM_SOURCE_ID, id: hoveredIdRef.current }, { hover: false });
      hoveredIdRef.current = nextId;
      if (nextId) map.setFeatureState({ source: GYM_SOURCE_ID, id: nextId }, { hover: true });
    });
    map.on("mouseleave", GYM_LAYER_ID, () => {
      map.getCanvas().style.cursor = "";
      if (hoveredIdRef.current && map.getSource(GYM_SOURCE_ID)) {
        map.setFeatureState({ source: GYM_SOURCE_ID, id: hoveredIdRef.current }, { hover: false });
      }
      hoveredIdRef.current = null;
    });
    map.on("error", (event) => {
      if (basemapRef.current === "osm" && event.error?.message) setMapError("Map tiles could not load. Try the OpenFreeMap vector option.");
    });
    map.on("sourcedata", (event) => {
      if (basemapRef.current === "openfreemap" && event.sourceId === "openmaptiles" && event.isSourceLoaded) clearFallbackTimer();
    });

    mapRef.current = map;
    return () => {
      clearFallbackTimer();
      originMarkerRef.current?.remove();
      map.remove();
      mapRef.current = null;
      setIsReady(false);
    };
    // The map instance is intentionally constructed once. Data and styles are
    // updated through MapLibre sources rather than React remounts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isReady) return;
    addGymSourceAndLayer(map, gyms);
    const source = map.getSource(GYM_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
    source?.setData(buildGymFeatureCollection(gyms));
    restoreFeatureStates(map);
  }, [gyms, isReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isReady) return;
    fitMapToGyms(map, gyms);
  }, [gyms, isReady]);

  useEffect(() => {
    const map = mapRef.current;
    selectedIdRef.current = selectedId;
    highlightedIdRef.current = highlightedId;
    if (!map || !isReady || !map.getSource(GYM_SOURCE_ID)) return;

    map.removeFeatureState({ source: GYM_SOURCE_ID });
    restoreFeatureStates(map);
    if (!selectedId) return;

    const gym = gymsByIdRef.current.get(selectedId);
    if (!gym) return;
    const isMobile = window.matchMedia("(max-width: 680px)").matches;
    map.flyTo({
      center: [gym.longitude, gym.latitude],
      zoom: Math.max(map.getZoom(), 14),
      offset: isMobile ? [0, -105] : [170, 0],
      duration: 500,
      essential: true,
    });
  }, [highlightedId, selectedId, isReady]);

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
      <div ref={containerRef} className="real-map" aria-label="Interactive map of San Francisco gyms" role="application" style={{ touchAction: "none" }} />
      <button type="button" className="map-recenter-control" onClick={recenterMap} aria-label="Re-center map" title="Re-center map">
        <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="5" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3" /></svg>
      </button>
      <div className="map-provider-control" style={{ pointerEvents: "none" }}>
        <span>{basemap === "openfreemap" ? "OpenFreeMap vector" : "OpenStreetMap streets"}</span>
        <button type="button" onClick={switchBasemap} style={{ pointerEvents: "auto" }}>{basemap === "openfreemap" ? "Use OSM streets" : "Try OpenFreeMap"}</button>
      </div>
      <div className="map-help" aria-hidden="true">Drag to explore - scroll to zoom - tap a dot for details</div>
      {mapError && <div className="map-status" role="status" style={{ pointerEvents: "none" }}>{mapError}</div>}
      {gyms.length === 0 && <div className="map-empty" style={{ pointerEvents: "none" }}>No venues match those filters. Try another type or neighborhood.</div>}
    </div>
  );
}
