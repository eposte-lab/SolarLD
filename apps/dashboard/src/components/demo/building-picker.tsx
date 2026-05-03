'use client';

/**
 * BuildingPicker — interactive map for the "which capannone is yours?" step.
 *
 * The Building Identification Cascade (BIC) returns this picker when its
 * automated cascade can't converge with high confidence on a single
 * building. The operator (or prospect) sees a satellite view of the
 * industrial zone with up to 5 ranked candidate pins, clicks the right
 * one, and we POST /v1/demo/confirm-building so subsequent runs for the
 * same VAT short-circuit at the cache layer.
 *
 * Flow:
 *   1. Receive `candidates` + the raw BIC response from the parent
 *      (test-pipeline-dialog) when /v1/demo/identify-building returned
 *      confidence in {low, none}.
 *   2. Render a Mapbox satellite map centered on the bbox of all
 *      candidates with one coloured marker per candidate.
 *   3. On click of a marker (or a freehand point), POST the picked
 *      lat/lng + the optional polygon to /v1/demo/confirm-building.
 *   4. Call onConfirmed(lat, lng) so the dialog can inject these
 *      coords into the eventual /v1/demo/test-pipeline submission and
 *      skip the cascade.
 *
 * Why a separate component: keeping the Mapbox GL JS init + cleanup
 * out of test-pipeline-dialog.tsx keeps that file small and lets us
 * lazy-load the bundle (mapbox-gl is ~600 KB minified) only when the
 * picker is actually shown.
 */

import 'mapbox-gl/dist/mapbox-gl.css';
import mapboxgl from 'mapbox-gl';
import { useEffect, useRef, useState } from 'react';
import { Check, Loader2, MapPin, Crosshair } from 'lucide-react';

import { createBrowserClient } from '@/lib/supabase/client';
import { cn } from '@/lib/utils';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export interface PickerCandidate {
  rank: number;
  lat: number;
  lng: number;
  weight: number;
  source: string;
  polygon_geojson: unknown | null;
  preview_url: string | null;
  metadata: Record<string, unknown>;
}

export interface BuildingPickerProps {
  vatNumber: string;
  candidates: PickerCandidate[];
  // Default centre when no candidates have coords (rare). Falls back to
  // the geocoded HQ centroid if available.
  fallbackCentre?: { lat: number; lng: number };
  onConfirmed: (lat: number, lng: number) => void;
  onCancel?: () => void;
}

// Colour-coded ring for each rank, matching the dashboard demo-runs
// roof badge convention: top match green, runner-ups amber, longshots grey.
const RANK_COLOURS = [
  '#10B981', // 1 — green
  '#F59E0B', // 2 — amber
  '#F59E0B', // 3 — amber
  '#9CA3AF', // 4 — grey
  '#9CA3AF', // 5 — grey
];
// Rank 6+ are OSM-zone buildings surfaced when the cascade can't
// auto-pick. They're rendered smaller and translucent so the eye is
// drawn to the ranked candidates first but the user can still click
// any of the 30 zone buildings if their capannone is among them.
const ZONE_PIN_COLOUR = '#6B7280'; // slate-500

async function authHeader(): Promise<Record<string, string>> {
  if (typeof window === 'undefined') return {};
  const sb = createBrowserClient();
  const {
    data: { session },
  } = await sb.auth.getSession();
  if (!session?.access_token) return {};
  return { Authorization: `Bearer ${session.access_token}` };
}

export function BuildingPicker({
  vatNumber,
  candidates,
  fallbackCentre,
  onConfirmed,
  onCancel,
}: BuildingPickerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const markersRef = useRef<mapboxgl.Marker[]>([]);
  const freehandMarkerRef = useRef<mapboxgl.Marker | null>(null);
  const [selected, setSelected] = useState<{ lat: number; lng: number; rank: number | null } | null>(
    null,
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [freehand, setFreehand] = useState(false);

  const token = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;

  // Initialise the Mapbox map once. The bbox is derived from all
  // candidates so we frame them all in view; if there's only one (or
  // none, which shouldn't happen but defend), we centre and zoom in.
  useEffect(() => {
    if (!token || !containerRef.current || mapRef.current) return;
    mapboxgl.accessToken = token;

    const coords =
      candidates.length > 0
        ? candidates.map((c) => [c.lng, c.lat] as [number, number])
        : fallbackCentre
          ? [[fallbackCentre.lng, fallbackCentre.lat] as [number, number]]
          : [[12.5, 41.9] as [number, number]];

    const map = new mapboxgl.Map({
      container: containerRef.current,
      style: 'mapbox://styles/mapbox/satellite-streets-v12',
      center: coords[0],
      // Zoom 15 fits a typical Italian Z.I. (~1×1 km) in the viewport so
      // the operator can scan the whole industrial cluster instead of
      // being dropped on a single rooftop and forced to pan to find
      // their building.
      zoom: 15,
      attributionControl: false,
    });
    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), 'top-right');

    map.on('load', () => {
      // Fit to bbox if multiple candidates — covers the case where we
      // have OSM-zone buildings (rank 6+) in addition to the ranked
      // candidates, so the bounds cover the whole industrial cluster.
      if (coords.length > 1) {
        const bounds = coords.reduce(
          (b, c) => b.extend(c),
          new mapboxgl.LngLatBounds(coords[0], coords[0]),
        );
        map.fitBounds(bounds, { padding: 60, maxZoom: 17, duration: 0 });
      }
    });

    // Freehand-pin support: click anywhere on the map to drop a custom pin
    // ("none of these — let me show you"). The handler reads `freehand`
    // from a ref-like closure via state, so we re-bind it inside the
    // freehand effect below rather than here.

    mapRef.current = map;
    return () => {
      markersRef.current.forEach((m) => m.remove());
      markersRef.current = [];
      freehandMarkerRef.current?.remove();
      freehandMarkerRef.current = null;
      map.remove();
      mapRef.current = null;
    };
    // We deliberately depend on the fallbackCentre identity *not* changing
    // mid-mount; the dialog re-mounts the picker on each open which
    // resets state cleanly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // Render markers for each candidate.
  useEffect(() => {
    if (!mapRef.current) return;
    const map = mapRef.current;
    markersRef.current.forEach((m) => m.remove());
    markersRef.current = [];

    candidates.forEach((cand) => {
      const isZonePin = cand.rank > 5;
      const colour = isZonePin
        ? ZONE_PIN_COLOUR
        : RANK_COLOURS[Math.min(cand.rank - 1, RANK_COLOURS.length - 1)];
      const el = document.createElement('button');
      el.type = 'button';
      // Zone pins are smaller (18 px vs 32 px) and have no rank label —
      // they're navigational aids, not ranked candidates.
      const size = isZonePin ? 18 : 32;
      el.style.width = `${size}px`;
      el.style.height = `${size}px`;
      el.style.borderRadius = '50%';
      el.style.border = `${isZonePin ? 2 : 3}px solid ${colour}`;
      el.style.background = isZonePin
        ? 'rgba(255,255,255,0.55)'
        : 'rgba(255,255,255,0.85)';
      el.style.color = '#0f172a';
      el.style.fontWeight = '700';
      el.style.fontSize = isZonePin ? '0' : '13px';
      el.style.cursor = 'pointer';
      el.style.boxShadow = isZonePin
        ? '0 1px 3px rgba(0,0,0,0.3)'
        : '0 2px 8px rgba(0,0,0,0.4)';
      el.textContent = isZonePin ? '' : String(cand.rank);
      el.title = isZonePin
        ? `Edificio nella zona · ${cand.source}`
        : `Candidato ${cand.rank} · ${cand.source} · peso ${cand.weight.toFixed(2)}`;
      el.onclick = (e) => {
        e.stopPropagation();
        setSelected({ lat: cand.lat, lng: cand.lng, rank: cand.rank });
        setFreehand(false);
        freehandMarkerRef.current?.remove();
        freehandMarkerRef.current = null;
      };
      const marker = new mapboxgl.Marker({ element: el, anchor: 'center' })
        .setLngLat([cand.lng, cand.lat])
        .addTo(map);
      markersRef.current.push(marker);
    });
  }, [candidates]);

  // Bind / unbind the freehand-click handler.
  useEffect(() => {
    if (!mapRef.current) return;
    const map = mapRef.current;
    if (!freehand) return;
    const onClick = (e: mapboxgl.MapMouseEvent) => {
      const { lng, lat } = e.lngLat;
      setSelected({ lat, lng, rank: null });
      // Drop a "freehand" custom marker.
      freehandMarkerRef.current?.remove();
      const el = document.createElement('div');
      el.style.width = '24px';
      el.style.height = '24px';
      el.style.borderRadius = '50%';
      el.style.background = '#3B82F6';
      el.style.border = '3px solid white';
      el.style.boxShadow = '0 2px 8px rgba(0,0,0,0.4)';
      const m = new mapboxgl.Marker({ element: el, anchor: 'center' })
        .setLngLat([lng, lat])
        .addTo(map);
      freehandMarkerRef.current = m;
    };
    map.on('click', onClick);
    return () => {
      map.off('click', onClick);
    };
  }, [freehand]);

  async function handleConfirm() {
    if (!selected) return;
    setSubmitting(true);
    setError(null);
    try {
      const auth = await authHeader();
      const polygon =
        selected.rank !== null
          ? candidates.find((c) => c.rank === selected.rank)?.polygon_geojson ?? null
          : null;
      const res = await fetch(`${API_URL}/v1/demo/confirm-building`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...auth },
        body: JSON.stringify({
          vat_number: vatNumber,
          lat: selected.lat,
          lng: selected.lng,
          polygon_geojson: polygon,
        }),
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => null)) as { detail?: string } | null;
        setError(data?.detail ?? `Errore (${res.status}). Riprova.`);
        return;
      }
      onConfirmed(selected.lat, selected.lng);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Errore imprevisto.');
    } finally {
      setSubmitting(false);
    }
  }

  if (!token) {
    return (
      <div className="rounded-xl bg-warning-container/40 p-4 text-xs text-on-warning-container">
        Mapbox token non configurato (<code>NEXT_PUBLIC_MAPBOX_TOKEN</code>).
        Imposta la variabile per abilitare il picker del capannone.
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-xl bg-surface-container-low p-3 ring-1 ring-on-surface/10">
      <div className="flex items-center justify-between">
        <p className="text-xs text-on-surface-variant">
          Il sistema non ha identificato con certezza il capannone. Clicca
          sul numero del candidato corretto, oppure attiva la modalità
          libera per indicare un punto custom.
        </p>
        <button
          type="button"
          className={cn(
            'inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-semibold ring-1 ring-on-surface/15',
            freehand
              ? 'bg-primary text-on-primary'
              : 'bg-surface text-on-surface hover:bg-surface-container-high',
          )}
          onClick={() => {
            setFreehand((v) => !v);
            if (freehand) {
              freehandMarkerRef.current?.remove();
              freehandMarkerRef.current = null;
            }
          }}
        >
          <Crosshair size={12} />
          {freehand ? 'Modalità libera attiva' : 'Nessuno: pinpoint libero'}
        </button>
      </div>

      <div
        ref={containerRef}
        className="h-[360px] w-full overflow-hidden rounded-xl ring-1 ring-on-surface/15"
      />

      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {candidates.slice(0, 5).map((cand) => (
          <button
            key={cand.rank}
            type="button"
            onClick={() => {
              setSelected({ lat: cand.lat, lng: cand.lng, rank: cand.rank });
              if (mapRef.current) {
                mapRef.current.easeTo({
                  center: [cand.lng, cand.lat],
                  zoom: 19,
                  duration: 600,
                });
              }
            }}
            className={cn(
              'flex items-center gap-2 rounded-lg p-2 text-left ring-1 transition',
              selected?.rank === cand.rank
                ? 'bg-primary/10 ring-primary'
                : 'bg-surface ring-on-surface/10 hover:ring-on-surface/30',
            )}
          >
            <span
              className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-bold"
              style={{
                background: 'rgba(255,255,255,0.85)',
                border: `3px solid ${RANK_COLOURS[Math.min(cand.rank - 1, RANK_COLOURS.length - 1)]}`,
                color: '#0f172a',
              }}
            >
              {cand.rank}
            </span>
            {cand.preview_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={cand.preview_url}
                alt={`Candidato ${cand.rank}`}
                className="h-12 w-12 shrink-0 rounded object-cover"
              />
            ) : (
              <div className="grid h-12 w-12 shrink-0 place-items-center rounded bg-surface-container-high">
                <MapPin size={16} className="text-on-surface-variant" />
              </div>
            )}
            <div className="min-w-0 flex-1">
              <p className="truncate text-xs font-semibold text-on-surface">
                Candidato {cand.rank} · {cand.source}
              </p>
              <p className="text-[10px] text-on-surface-variant">
                Peso {cand.weight.toFixed(2)} · {cand.lat.toFixed(5)},{' '}
                {cand.lng.toFixed(5)}
              </p>
            </div>
          </button>
        ))}
      </div>

      {error && (
        <p className="rounded-lg bg-error-container px-3 py-2 text-xs text-on-error-container">
          {error}
        </p>
      )}

      <div className="flex items-center justify-end gap-2">
        {onCancel && (
          <button
            type="button"
            onClick={onCancel}
            disabled={submitting}
            className="rounded-full px-3 py-1.5 text-xs font-semibold text-on-surface-variant hover:bg-surface-container-high disabled:opacity-50"
          >
            Annulla
          </button>
        )}
        <button
          type="button"
          onClick={handleConfirm}
          disabled={!selected || submitting}
          className="inline-flex items-center gap-1.5 rounded-full bg-primary px-4 py-2 text-xs font-semibold text-on-primary shadow-ambient-sm hover:shadow-ambient-md disabled:opacity-50"
        >
          {submitting ? (
            <>
              <Loader2 size={14} strokeWidth={2.5} className="animate-spin" />
              Salvo…
            </>
          ) : (
            <>
              <Check size={14} strokeWidth={2.5} />
              Conferma capannone
            </>
          )}
        </button>
      </div>
    </div>
  );
}
