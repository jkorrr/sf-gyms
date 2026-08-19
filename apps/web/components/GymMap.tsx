"use client";

import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";

import type { Gym } from "../lib/demo-data";
import type { GeoPoint } from "../lib/geo";

const OPENFREEMAP_STYLE = "https://tiles.openfreemap.org/styles/liberty";
const SF_CENTER: [number, number] = [-122.4194, 37.7749];
const GYMS_SOURCE_ID = "sf-gyms";
const GYMS_LAYER_ID = "sf-gyms-dots";
const SELECTED_GYM_LAYER_ID = "sf-gyms-selected-dot";
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

function gymsGeoJson(gyms: Gym[]) {
  return {
    type: "FeatureCollection" as const,
    features: gyms.map((gym) => ({
      type: "Feature" as const,
      id: gym.id,
      geometry: {
        type: "Point" as const,
        coordinates: [gym.longitude, gym.latitude],
      },
      properties: {
        gymId: gym.id,
        name: gym.name,
      },
    })),
  };
}

function syncGymLayers(map: maplibregl.Map, gyms: Gym[], selectedId?: string) {
  if (!map.isStyleLoaded()) return;

  const data = gymsGeoJson(gyms);
  const source = map.getSource(GYMS_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;

  if (source) {
    source.setData(data);
  } else {
    map.addSource(GYMS_SOURCE_ID, { type: "geojson", data });
    map.addLayer({
      id: GYMS_LAYER_ID,
      type: "circle",
      source: GYMS_SOURCE_ID,
      paint: {
        "circle-radius": 10,
        "circle-color": "#75a789",
        "circle-stroke-color": "#ffffff",
        "circle-stroke-width": 3,
        "circle-opacity": 0.96,
      },
    });
    map.addLayer({
      id: SELECTED_GYM_LAYER_ID,
      type: "circle",
      source: GYMS_SOURCE_ID,
      filter: ["==", ["get", "gymId"], selectedId ?? ""],
      paint: {
        "circle-radius": 13,
        "circle-color": "#4f8262",
        "circle-stroke-color": "#d8eadf",
        "circle-stroke-width": 6,
      },
    });
  }

  if (map.getLayer(SELECTED_GYM_LAYER_ID)) {
    map.setFilter(SELECTED_GYM_LAYER_ID, ["==", ["get", "gymId"], selectedId ?? ""]);
  }
}

export default function GymMap({ gyms, selectedId, origin, onSelect }: GymMapProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const gymsRef = useRef(gyms);
  const gymsByIdRef = useRef(new globalThis.Map(gyms.map((gym) => [gym.id, gym])));
  const selectedIdRef = useRef(selectedId);
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
    selectedIdRef.current = selectedId;
  }, [selectedId]);

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
    const restoreGymLayers = () => {
      syncGymLayers(map, gymsRef.current, selectedIdRef.current);
      map.resize();
      setIsReady(true);
    };
    map.on("load", restoreGymLayers);
    map.on("style.load", restoreGymLayers);
    map.on("click", (event) => {
      const layers = [GYMS_LAYER_ID, SELECTED_GYM_LAYER_ID].filter((id) => map.getLayer(id));
      const feature = layers.length
        ? map.queryRenderedFeatures(event.point, { layers }).find((candidate) => candidate.properties?.gymId)
        : undefined;
      const gym = feature ? gymsByIdRef.current.get(String(feature.properties?.gymId)) : undefined;

      if (!gym) {
        onSelectRef.current(null);
        return;
      }

      onSelectRef.current(gym);
      map.flyTo({ center: [gym.longitude, gym.latitude], zoom: Math.max(map.getZoom(), 14), duration: 500, essential: true });
    });
    map.on("mousemove", (event) => {
      if (!map.getLayer(GYMS_LAYER_ID)) return;
      const isOverGym = map.queryRenderedFeatures(event.point, { layers: [GYMS_LAYER_ID, SELECTED_GYM_LAYER_ID] }).length > 0;
      map.getCanvas().style.cursor = isOverGym ? "pointer" : "";
    });
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
    syncGymLayers(map, gyms, selectedIdRef.current);
  }, [gyms, isReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isReady || gyms.length === 0) return;
    const bounds = new maplibregl.LngLatBounds();
    gyms.forEach((gym) => bounds.extend([gym.longitude, gym.latitude]));
    map.fitBounds(bounds, { padding: { top: 170, right: 44, bottom: 250, left: 44 }, maxZoom: 14, duration: 500 });
  }, [gyms, isReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !isReady) return;
    syncGymLayers(map, gymsRef.current, selectedId);
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
      {gyms.length === 0 && <div className="map-empty" style={{ pointerEvents: "none" }}>No venues match those filters. Try another type or neighborhood.</div>}
    </div>
  );
}
