'use client';

/**
 * RoofDelineationTool — operator draws the REAL usable roof area on the aerial;
 * the system keeps only the Google Solar panels inside the polygon and
 * recomputes kWp / ROI from that subset (Feature 2). For warm/hot leads where a
 * precise quote is worth it.
 *
 * Mapbox satellite + a circle overlay of every Solar panel + a polygon draw
 * tool. Each polygon edit calls POST /v1/leads/:id/roof-delineation?dry_run
 * (preview, no writes); "Salva" persists the override + the recomputed numbers
 * so they flow to the dossier/email. Server does the point-in-polygon +
 * recompute (shapely); this is pure UI.
 */

import 'mapbox-gl/dist/mapbox-gl.css';
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css';

import MapboxDraw from '@mapbox/mapbox-gl-draw';
import mapboxgl from 'mapbox-gl';
import { Check, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import { api, ApiError } from '@/lib/api-client';

type Panel = { lat: number; lng: number };
type Preview = {
  kept_panel_count: number;
  total_panel_count: number;
  estimated_kwp: number;
  panel_area_sqm: number;
  yearly_savings_eur: number | null;
  payback_years: number | null;
};

export function RoofDelineationTool({
  leadId,
  lat,
  lng,
  panels,
  currentKwp,
}: {
  leadId: string;
  lat: number;
  lng: number;
  panels: Panel[];
  currentKwp: number | null;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const drawRef = useRef<MapboxDraw | null>(null);
  // Latest drawn polygon geometry (GeoJSON Polygon), for "Salva".
  const polyRef = useRef<GeoJSON.Polygon | null>(null);

  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const token = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;

  useEffect(() => {
    if (!token || !containerRef.current || mapRef.current) return;
    mapboxgl.accessToken = token;

    const map = new mapboxgl.Map({
      container: containerRef.current,
      style: 'mapbox://styles/mapbox/satellite-streets-v12',
      center: [lng, lat],
      zoom: 18.5,
      attributionControl: false,
    });
    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), 'top-right');

    const draw = new MapboxDraw({
      displayControlsDefault: false,
      controls: { polygon: true, trash: true },
    });
    map.addControl(draw, 'top-left');
    drawRef.current = draw;

    map.on('load', () => {
      // Plot every Solar panel as a small circle (cheap for hundreds of points).
      map.addSource('solar-panels', {
        type: 'geojson',
        data: {
          type: 'FeatureCollection',
          features: panels.map((p) => ({
            type: 'Feature',
            geometry: { type: 'Point', coordinates: [p.lng, p.lat] },
            properties: {},
          })),
        },
      });
      map.addLayer({
        id: 'solar-panels-layer',
        type: 'circle',
        source: 'solar-panels',
        paint: {
          'circle-radius': 3,
          'circle-color': '#2563eb',
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 0.5,
          'circle-opacity': 0.85,
        },
      });
    });

    const onChange = () => {
      const all = draw.getAll();
      const poly = all.features.find((f) => f.geometry.type === 'Polygon');
      if (!poly || poly.geometry.type !== 'Polygon') {
        polyRef.current = null;
        setPreview(null);
        return;
      }
      polyRef.current = poly.geometry;
      void runPreview(poly.geometry);
    };
    // Custom Draw events aren't in mapbox-gl's event map → cast.
    map.on('draw.create' as 'load', onChange);
    map.on('draw.update' as 'load', onChange);
    map.on('draw.delete' as 'load', onChange);

    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      drawRef.current = null;
    };
    // Mount once; the lead page re-mounts on navigation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function runPreview(geometry: GeoJSON.Polygon) {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const res = await api.post<Preview>(`/v1/leads/${leadId}/roof-delineation`, {
        polygon_geojson: geometry,
        dry_run: true,
      });
      setPreview(res);
    } catch (err) {
      setPreview(null);
      setError(
        err instanceof ApiError ? err.message : 'Errore nel calcolo. Ridisegna l’area.',
      );
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!polyRef.current) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.post<Preview>(`/v1/leads/${leadId}/roof-delineation`, {
        polygon_geojson: polyRef.current,
        dry_run: false,
      });
      setPreview(res);
      setSaved(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Salvataggio non riuscito.');
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <p className="rounded-lg bg-surface-container-low p-4 text-sm text-on-surface-variant">
        Mappa non disponibile: <code>NEXT_PUBLIC_MAPBOX_TOKEN</code> non configurato.
      </p>
    );
  }

  const eur = (n: number | null | undefined) =>
    n != null ? `€${Math.round(n).toLocaleString('it-IT')}` : '—';

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_260px]">
      <div
        ref={containerRef}
        className="h-[440px] w-full overflow-hidden rounded-xl border border-outline-variant"
      />

      <div className="space-y-4">
        <p className="text-sm text-on-surface-variant">
          Disegna l’area reale del tetto col tasto poligono (in alto a sinistra).
          Calcoliamo i numeri sui pannelli dentro l’area.
        </p>

        <div className="rounded-xl bg-surface-container-low p-4">
          <div className="flex items-baseline justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              kWp attuale
            </span>
            <span className="font-headline text-lg font-bold tabular-nums text-on-surface-variant">
              {currentKwp ?? '—'}
            </span>
          </div>
          <div className="mt-3 flex items-baseline justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              kWp nell’area
            </span>
            <span className="font-headline text-2xl font-bold tabular-nums text-primary">
              {busy ? '…' : (preview?.estimated_kwp ?? '—')}
            </span>
          </div>
          {preview && (
            <dl className="mt-3 space-y-1.5 text-sm">
              <Row label="Pannelli" value={`${preview.kept_panel_count} / ${preview.total_panel_count}`} />
              <Row label="Risparmio/anno" value={eur(preview.yearly_savings_eur)} />
              <Row
                label="Rientro"
                value={preview.payback_years != null ? `${preview.payback_years} anni` : '—'}
              />
            </dl>
          )}
        </div>

        <button
          type="button"
          onClick={save}
          disabled={busy || !preview}
          className="w-full rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-on-primary transition-opacity hover:opacity-90 disabled:opacity-50"
        >
          {busy ? 'Salvataggio…' : 'Salva preventivo mirato'}
        </button>

        {saved && (
          <p className="inline-flex items-center gap-1.5 text-xs font-semibold text-primary">
            <Check size={13} strokeWidth={2.5} aria-hidden />
            Salvato. Il dossier riflette i nuovi numeri.
          </p>
        )}
        {error && (
          <p className="inline-flex items-start gap-1.5 text-xs font-semibold text-error">
            <X size={13} strokeWidth={2.5} className="mt-0.5 shrink-0" aria-hidden />
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between">
      <dt className="text-on-surface-variant">{label}</dt>
      <dd className="font-semibold tabular-nums text-on-surface">{value}</dd>
    </div>
  );
}
