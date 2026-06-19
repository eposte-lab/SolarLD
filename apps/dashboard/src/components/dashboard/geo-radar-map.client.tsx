'use client';

/**
 * GeoRadarMapClient — interactive Italy lead map powered by Mapbox GL.
 *
 * Visual spec (Editorial Glass — Sprint 7):
 *   - Satellite-streets base style with dark tint overlay (sat-v9 + black @55%)
 *   - Markers usano la nuova palette: amber per hot/appointment,
 *     success per won, dim grey per default. Pulse ring sempre amber.
 *   - Popup on hover: province name, total / hot / appointment / won counts
 *   - Graceful fallback when NEXT_PUBLIC_MAPBOX_TOKEN is absent
 */

import mapboxgl from 'mapbox-gl';
import 'mapbox-gl/dist/mapbox-gl.css';
import { useEffect, useRef, useState } from 'react';

import type { GeoLeadPin, ProvinceAggregate } from '@/lib/data/geo-analytics';
import { engagementTier } from '@/lib/data/engagement-shared';
import { getCentroid } from './italy-provinces';

// ── colour helpers ────────────────────────────────────────────────────────────

// Liquid Glass palette: green single-accent + warning amber + dim grey.
const COLOR_MINT = '#22C55E'; // firmato / won
const COLOR_MINT_DIM = '#16A34A';
const COLOR_WARNING = '#F4A45C'; // hot
const COLOR_DIM = '#8A9499';
const COLOR_CONVO = '#3B82F6'; // in conversazione (dialogo attivo)
const COLOR_INTERESTED = '#14B8A6'; // interessato (engaged, non ancora hot)
const COLOR_LOST = '#6B7280'; // perso (closed_lost)

function dominantColor(agg: ProvinceAggregate): string {
  if (agg.won > 0) return COLOR_MINT;
  if (agg.appointments > 0) return COLOR_MINT_DIM;
  if (agg.hot > 0) return COLOR_WARNING;
  return COLOR_DIM;
}

export interface LeadPinState {
  key: string;
  label: string;
  color: string;
}

/**
 * The lead's ENGAGEMENT-funnel state — what the operator actually wants on the
 * map. Priority = most-advanced stage wins. "Hot" keys off the behavioural
 * ``engagement_score`` (≥60, mirrors engagementTier), NOT the static discovery
 * ``score_tier`` (the old, misleading rule that mislabelled the legend).
 */
function pinState(pin: GeoLeadPin): LeadPinState {
  const st = pin.pipeline_status;
  if (st === 'closed_won') return { key: 'firmato', label: 'Firmato', color: COLOR_MINT };
  if (st === 'closed_lost') return { key: 'perso', label: 'Perso', color: COLOR_LOST };
  if (st === 'whatsapp' || st === 'appointment')
    return { key: 'conversazione', label: 'In conversazione', color: COLOR_CONVO };
  if (engagementTier(pin.engagement_score) === 'hot')
    return { key: 'hot', label: 'Hot', color: COLOR_WARNING };
  return { key: 'interessato', label: 'Interessato', color: COLOR_INTERESTED };
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c] ?? c,
  );
}

function markerSize(count: number): number {
  // Scale: 1 lead → 10px, 50+ leads → 40px (logarithmic)
  return Math.max(10, Math.min(40, 10 + Math.log10(count + 1) * 14));
}

// ── component ─────────────────────────────────────────────────────────────────

export interface GeoRadarMapClientProps {
  aggregates: ProvinceAggregate[];
  /** Per-lead precise pins (preferred). When present, the map plots one
   *  marker at each rooftop's lat/lng instead of one blob per province. */
  pins?: GeoLeadPin[];
  /** Optional: pre-selected province code (highlights that marker). */
  selectedProvincia?: string;
}

export function GeoRadarMapClient({
  aggregates,
  pins,
  selectedProvincia,
}: GeoRadarMapClientProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<mapboxgl.Map | null>(null);
  const markersRef = useRef<mapboxgl.Marker[]>([]);
  const [ready, setReady] = useState(false);
  const token = process.env.NEXT_PUBLIC_MAPBOX_TOKEN;

  useEffect(() => {
    if (!token || !containerRef.current || mapRef.current) return;

    mapboxgl.accessToken = token;

    const map = new mapboxgl.Map({
      container: containerRef.current,
      style: 'mapbox://styles/mapbox/satellite-streets-v12',
      center: [12.5, 41.9], // Italy centre
      zoom: 4.8,
      minZoom: 3,
      maxZoom: 10,
      attributionControl: false,
      logoPosition: 'bottom-right',
    });

    map.addControl(
      new mapboxgl.AttributionControl({ compact: true }),
      'bottom-right',
    );
    map.addControl(new mapboxgl.NavigationControl({ showCompass: false }), 'top-right');

    map.on('load', () => {
      // Editorial dark tint: scurire la satellite imagery sovrapponendo
      // un layer pieno scuro a 55% opacity sopra il "background" mapbox.
      // Il risultato è "satellite ma cinema-dark" come i reference RonDesignLab.
      try {
        const layers = map.getStyle()?.layers ?? [];
        const firstLabelLayer = layers.find(
          (l) => l.type === 'symbol' && l.id.includes('label'),
        );
        map.addLayer(
          {
            id: 'editorial-dark-tint',
            type: 'background',
            paint: {
              'background-color': '#07090A',
              'background-opacity': 0.58,
            },
          },
          firstLabelLayer?.id,
        );
      } catch {
        // Style mutation can fail in older mapbox versions or offline mode;
        // mappa resta funzionante (solo meno editorial).
      }
      setReady(true);
    });

    mapRef.current = map;
    return () => {
      markersRef.current.forEach((m) => m.remove());
      markersRef.current = [];
      map.remove();
      mapRef.current = null;
    };
  }, [token]);

  // Re-draw markers whenever aggregates change
  useEffect(() => {
    if (!ready || !mapRef.current) return;

    // Remove old markers
    markersRef.current.forEach((m) => m.remove());
    markersRef.current = [];

    const popup = new mapboxgl.Popup({
      closeButton: false,
      closeOnClick: false,
      className: 'geo-popup',
      maxWidth: '240px',
    });

    // ── Preferred: one precise marker per active lead (rooftop lat/lng) ──
    if (pins && pins.length > 0) {
      for (const pin of pins) {
        const state = pinState(pin);
        const el = document.createElement('div');
        el.className = 'geo-marker';
        el.style.cssText = `
          width: 12px; height: 12px; border-radius: 50%;
          background-color: ${state.color};
          border: 1.5px solid rgba(255,255,255,0.85);
          box-shadow: 0 0 0 1px rgba(0,0,0,0.30);
          cursor: pointer;
        `;
        el.setAttribute('role', 'button');
        el.setAttribute('tabindex', '0');
        el.setAttribute('aria-label', `${pin.business_name || pin.comune || 'Lead'} — ${state.label}`);
        el.title = `${pin.business_name || pin.comune || 'Lead'} · ${state.label}`;
        const marker = new mapboxgl.Marker({ element: el, anchor: 'center' })
          .setLngLat([pin.lng, pin.lat])
          .addTo(mapRef.current!);

        el.addEventListener('mouseenter', () => {
          const label = pin.business_name || pin.comune || 'Lead';
          const meta = [
            pin.comune ? escapeHtml(pin.comune) : null,
            `engagement ${pin.engagement_score}/100`,
          ]
            .filter(Boolean)
            .join(' · ');
          const dot = `<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background-color:${state.color};margin-right:6px;vertical-align:middle"></span>`;
          const content = `
            <div style="font-family:'Plus Jakarta Sans',sans-serif;font-size:12px;line-height:1.5">
              <div style="font-weight:700;font-size:13px;margin-bottom:5px;letter-spacing:-0.01em">${escapeHtml(label)}</div>
              <div style="color:#ECEFF0;font-weight:600;margin-bottom:3px">${dot}${state.label}</div>
              <div style="color:#8A9499">${meta}</div>
              <div style="color:#8A9499;margin-top:6px;font-size:11px;font-weight:600">Clicca per aprire la scheda →</div>
            </div>`;
          popup.setLngLat([pin.lng, pin.lat]).setHTML(content).addTo(mapRef.current!);
        });
        el.addEventListener('mouseleave', () => popup.remove());
        // Click / Enter → open the full lead sheet in a new tab (keeps the map).
        const openLead = () =>
          window.open(`/leads/${pin.id}`, '_blank', 'noopener,noreferrer');
        el.addEventListener('click', openLead);
        el.addEventListener('keydown', (ev) => {
          if (ev.key === 'Enter' || ev.key === ' ') {
            ev.preventDefault();
            openLead();
          }
        });
        markersRef.current.push(marker);
      }
      return;
    }

    for (const agg of aggregates) {
      const centroid = getCentroid(agg.provincia);
      const size = markerSize(agg.total);
      const color = dominantColor(agg);
      const isPulsing = agg.hot > 0 || agg.appointments > 0;
      const isSelected = agg.provincia === selectedProvincia;

      // Build custom HTML element
      const el = document.createElement('div');
      el.className = 'geo-marker';
      el.style.cssText = `
        width: ${size}px;
        height: ${size}px;
        border-radius: 50%;
        background-color: ${color};
        opacity: ${isSelected ? 1 : 0.85};
        border: 2px solid ${isSelected ? '#fff' : 'transparent'};
        cursor: pointer;
        position: relative;
        display: flex;
        align-items: center;
        justify-content: center;
      `;

      // Count badge for large clusters
      if (agg.total >= 5) {
        const badge = document.createElement('span');
        badge.style.cssText = `
          font-size: 9px;
          font-weight: 700;
          color: #fff;
          pointer-events: none;
          text-shadow: 0 1px 2px rgba(0,0,0,0.6);
          font-family: 'Plus Jakarta Sans', sans-serif;
        `;
        badge.textContent = agg.total >= 1000
          ? `${(agg.total / 1000).toFixed(1)}k`
          : String(agg.total);
        el.appendChild(badge);
      }

      // Pulse ring per province con hot/appointment — mint editoriale
      if (isPulsing) {
        const ring = document.createElement('div');
        ring.style.cssText = `
          position: absolute;
          inset: -6px;
          border-radius: 50%;
          border: 2px solid ${COLOR_MINT};
          animation: radarPulse 2s ease-out infinite;
          pointer-events: none;
          opacity: 0.7;
        `;
        el.appendChild(ring);

        const ring2 = document.createElement('div');
        ring2.style.cssText = `
          position: absolute;
          inset: -6px;
          border-radius: 50%;
          border: 1.5px solid ${COLOR_MINT};
          animation: radarPulse 2s ease-out infinite;
          animation-delay: 0.8s;
          pointer-events: none;
          opacity: 0.4;
        `;
        el.appendChild(ring2);
      }

      const marker = new mapboxgl.Marker({ element: el, anchor: 'center' })
        .setLngLat([centroid.lng, centroid.lat])
        .addTo(mapRef.current!);

      // Hover popup — niente emoji, dot indicator + label sobria
      el.addEventListener('mouseenter', () => {
        const dot = (color: string) =>
          `<span style="display:inline-block;width:6px;height:6px;border-radius:50%;background-color:${color};margin-right:8px;vertical-align:middle"></span>`;
        const content = `
          <div style="font-family:'Plus Jakarta Sans',sans-serif;font-size:12px;line-height:1.6">
            <div style="font-weight:700;font-size:13px;margin-bottom:6px;letter-spacing:-0.01em">
              ${centroid.name}
            </div>
            <div style="color:#8A9499;display:flex;flex-direction:column;gap:3px">
              <span>${dot('#8A9499')}${agg.total} lead totali</span>
              ${agg.hot > 0 ? `<span style="color:#ECEFF0">${dot(COLOR_WARNING)}${agg.hot} hot</span>` : ''}
              ${agg.appointments > 0 ? `<span style="color:#ECEFF0">${dot(COLOR_MINT_DIM)}${agg.appointments} appuntamenti</span>` : ''}
              ${agg.won > 0 ? `<span style="color:#ECEFF0">${dot(COLOR_MINT)}${agg.won} firmati</span>` : ''}
            </div>
          </div>
        `;
        popup.setLngLat([centroid.lng, centroid.lat]).setHTML(content).addTo(mapRef.current!);
      });

      el.addEventListener('mouseleave', () => {
        popup.remove();
      });

      markersRef.current.push(marker);
    }
  }, [ready, aggregates, pins, selectedProvincia]);

  // ── no token → static fallback ─────────────────────────────────────────────
  if (!token) {
    return (
      <ProvinceBarFallback aggregates={aggregates} />
    );
  }

  return (
    <div className="relative h-full w-full overflow-hidden rounded-xl">
      <div ref={containerRef} className="h-full w-full" />
      {/* Loading shimmer */}
      {!ready && (
        <div className="absolute inset-0 animate-pulse rounded-xl bg-surface-container-high" />
      )}
    </div>
  );
}

// ── no-token fallback: static bar chart by province ────────────────────────────

function ProvinceBarFallback({ aggregates }: { aggregates: ProvinceAggregate[] }) {
  const sorted = [...aggregates].sort((a, b) => b.total - a.total).slice(0, 10);
  const maxTotal = Math.max(1, sorted[0]?.total ?? 1);

  return (
    <div className="flex h-full flex-col gap-1.5 overflow-hidden p-1">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
        Top province per lead
      </p>
      {sorted.map((agg) => (
        <div key={agg.provincia} className="flex items-center gap-2">
          <span className="w-7 shrink-0 font-headline text-xs font-bold text-on-surface">
            {agg.provincia}
          </span>
          <div className="flex-1 overflow-hidden rounded-full bg-surface-container-high">
            <div
              className="h-2 rounded-full transition-all duration-500"
              style={{
                width: `${(agg.total / maxTotal) * 100}%`,
                backgroundColor: dominantColor(agg),
              }}
            />
          </div>
          <span className="w-6 shrink-0 text-right text-[10px] tabular-nums text-on-surface-variant">
            {agg.total}
          </span>
        </div>
      ))}
      <p className="mt-auto text-[10px] text-on-surface-variant/60">
        Imposta NEXT_PUBLIC_MAPBOX_TOKEN per la mappa interattiva
      </p>
    </div>
  );
}
