/**
 * Leaflet map with OpenStreetMap tiles.
 *
 * OSM tiles need no API key, which keeps the browser bundle free of
 * credentials entirely. Attribution is required by the OSM tile usage policy
 * and is rendered by Leaflet itself.
 *
 * If you switch to a commercial tile provider, its browser key must be
 * referrer-restricted in that provider's console -- a browser key is public
 * by design and restriction is the only thing protecting it.
 */

import { useEffect, useMemo } from 'react';
import { MapContainer, Marker, Polyline, Popup, TileLayer, useMap } from 'react-leaflet';
import L from 'leaflet';
import type { Coordinates } from '../lib/types';
import { distance as formatDistance } from '../lib/format';

export interface MapPoint {
  coordinates: Coordinates;
  label: string;
  detail?: string;
  kind?: 'origin' | 'destination' | 'stop';
  order?: number;
}

/** Inline SVG pins, so no marker images need to be bundled or fetched. */
function pinIcon(kind: MapPoint['kind'], order?: number): L.DivIcon {
  const palette: Record<string, string> = {
    origin: '#475569',
    destination: '#0d9488',
    stop: '#d97706',
  };
  const colour = palette[kind ?? 'stop'] ?? palette.stop;
  const text = kind === 'origin' ? '◉' : order !== undefined ? String(order) : '';

  return L.divIcon({
    className: 'tripwise-pin',
    html: `
      <div style="
        width:26px;height:26px;border-radius:50% 50% 50% 0;
        background:${colour};transform:rotate(-45deg);
        border:2px solid #fff;box-shadow:0 2px 5px rgba(0,0,0,.35);
        display:flex;align-items:center;justify-content:center;">
        <span style="transform:rotate(45deg);color:#fff;font-size:11px;font-weight:700;
                     font-family:system-ui,sans-serif;line-height:1;">${text}</span>
      </div>`,
    iconSize: [26, 26],
    iconAnchor: [13, 26],
    popupAnchor: [0, -24],
  });
}

/** Keep every point in view whenever the set of points changes. */
function FitBounds({ points }: { points: MapPoint[] }) {
  const map = useMap();
  const signature = points.map((p) => `${p.coordinates.lat},${p.coordinates.lon}`).join('|');

  useEffect(() => {
    if (!points.length) return;
    if (points.length === 1) {
      const only = points[0];
      if (only) map.setView([only.coordinates.lat, only.coordinates.lon], 11);
      return;
    }
    const bounds = L.latLngBounds(
      points.map((p) => [p.coordinates.lat, p.coordinates.lon] as [number, number]),
    );
    map.fitBounds(bounds, { padding: [34, 34], maxZoom: 13 });
    // `signature` captures the real dependency; the array identity changes
    // on every render and would re-fit the map constantly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, map]);

  return null;
}

interface Props {
  points: MapPoint[];
  /** Draw a dashed line through the stops, in order. */
  connect?: boolean;
  height?: number;
  interactive?: boolean;
  ariaLabel?: string;
}

export function MapView({
  points,
  connect = false,
  height = 240,
  interactive = true,
  ariaLabel = 'Map of the trip',
}: Props) {
  const polyline = useMemo(
    () =>
      connect
        ? points.map((p) => [p.coordinates.lat, p.coordinates.lon] as [number, number])
        : [],
    [connect, points],
  );

  const centre = points[0]?.coordinates ?? { lat: 20.59, lon: 78.96 };

  if (!points.length) {
    return (
      <div
        className="map-frame row"
        style={{ height, justifyContent: 'center' }}
        role="img"
        aria-label="No map locations available"
      >
        <span className="subtle">No map locations available</span>
      </div>
    );
  }

  return (
    <div className="map-frame" style={{ height }} role="region" aria-label={ariaLabel}>
      <MapContainer
        center={[centre.lat, centre.lon]}
        zoom={6}
        style={{ height: '100%', width: '100%' }}
        scrollWheelZoom={false}
        dragging={interactive}
        zoomControl={interactive}
        doubleClickZoom={interactive}
        attributionControl
      >
        <TileLayer
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          maxZoom={18}
        />
        {polyline.length > 1 ? (
          <Polyline
            positions={polyline}
            pathOptions={{ color: '#0d9488', weight: 2.5, dashArray: '6 6', opacity: 0.8 }}
          />
        ) : null}
        {points.map((point, index) => (
          <Marker
            key={`${point.label}-${index}`}
            position={[point.coordinates.lat, point.coordinates.lon]}
            icon={pinIcon(point.kind, point.order)}
          >
            <Popup>
              <strong>{point.label}</strong>
              {point.detail ? (
                <>
                  <br />
                  <span style={{ fontSize: '0.85em' }}>{point.detail}</span>
                </>
              ) : null}
            </Popup>
          </Marker>
        ))}
        <FitBounds points={points} />
      </MapContainer>
    </div>
  );
}

/**
 * A static, dependency-free route sketch.
 *
 * Used on recommendation cards, where mounting one Leaflet instance per card
 * would be wasteful. It draws the great-circle hop as an SVG rather than
 * pretending to show a real road route.
 */
export function RouteSketch({
  originLabel,
  destinationLabel,
  distanceKm,
  durationHours,
  modeIcon,
}: {
  originLabel: string;
  destinationLabel: string;
  distanceKm: number;
  durationHours: number;
  modeIcon: string;
}) {
  const hours = Math.round(durationHours * 10) / 10;
  return (
    <div
      className="panel"
      role="img"
      aria-label={`${originLabel} to ${destinationLabel}, ${formatDistance(
        distanceKm,
      )}, about ${hours} hours each way`}
    >
      <div className="row" style={{ justifyContent: 'space-between', flexWrap: 'nowrap' }}>
        <span className="xs strong" style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {originLabel}
        </span>
        <span className="xs muted" style={{ flexShrink: 0 }} aria-hidden="true">
          {modeIcon}
        </span>
        <span className="xs strong" style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', textAlign: 'right' }}>
          {destinationLabel}
        </span>
      </div>
      <svg
        viewBox="0 0 200 18"
        style={{ width: '100%', height: 18, marginBlock: 4 }}
        aria-hidden="true"
      >
        <line x1="4" y1="9" x2="196" y2="9" stroke="var(--border-strong)" strokeWidth="1.5" strokeDasharray="4 4" />
        <circle cx="4" cy="9" r="3.5" fill="var(--ink-500)" />
        <circle cx="196" cy="9" r="4" fill="var(--brand)" />
      </svg>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <span className="xs muted tnum">{formatDistance(distanceKm)} each way</span>
        <span className="xs muted tnum">~{hours} h each way</span>
      </div>
    </div>
  );
}
