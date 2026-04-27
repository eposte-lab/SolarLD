/**
 * SolarApiInspector — read-only inspection of the Google Solar API
 * data that drives this lead's quote.
 *
 * Surfaces every input the Creative agent sees:
 *   - panel count + max array panel count (capacity ceiling)
 *   - estimated kWp + yearly kWh (derived from panel count × 410 W)
 *   - dominant exposure / azimuth / pitch / shading
 *   - per-segment area + azimuth (so the operator can sanity-check
 *     "did Solar pick the right side of an L-shaped roof?")
 *
 * Why this matters: the AI paint step (nano-banana) trusts those
 * numbers blindly. If Solar API returned a stale aerial or picked the
 * wrong segment, the AI paints panels in the wrong place AND the ROI
 * quote attached to the email is wrong. Showing the raw inputs lets
 * the operator catch this before the email goes out.
 *
 * The companion `RegenerateRenderingButton` (when bundled in the same
 * card) lets them re-trigger the pipeline if the data looks off — but
 * deeper editing (manual override of panel_count / kWp on the lead)
 * is a future iteration.
 */

import type { LeadDetailRow } from '@/types/db';
import { formatNumber } from '@/lib/utils';

import { BentoCard } from '@/components/ui/bento-card';
import { RegenerateRenderingButton } from './RegenerateRenderingButton';

interface Props {
  lead: LeadDetailRow;
}

interface SolarPanelEntry {
  yearlyEnergyDcKwh?: number;
  segmentIndex?: number;
  orientation?: string;
}

interface RoofSegmentStats {
  azimuthDegrees?: number;
  pitchDegrees?: number;
  stats?: { areaMeters2?: number };
}

interface SolarPotential {
  maxArrayPanelsCount?: number;
  panelCapacityWatts?: number;
  panelHeightMeters?: number;
  panelWidthMeters?: number;
  solarPanels?: SolarPanelEntry[];
  roofSegmentStats?: RoofSegmentStats[];
}

interface BuildingInsightsRaw {
  solarPotential?: SolarPotential;
}

/** Convert 0-360° azimuth → human compass label. Mirrors backend logic. */
function azimuthToCompass(deg: number): string {
  const d = ((deg % 360) + 360) % 360;
  if (d >= 337.5 || d < 22.5) return 'N';
  if (d < 67.5) return 'NE';
  if (d < 112.5) return 'E';
  if (d < 157.5) return 'SE';
  if (d < 202.5) return 'S';
  if (d < 247.5) return 'SW';
  if (d < 292.5) return 'W';
  return 'NW';
}

export function SolarApiInspector({ lead }: Props) {
  const roof = lead.roofs;
  const raw = (roof?.raw_data ?? null) as BuildingInsightsRaw | null;
  const potential = raw?.solarPotential;

  // Guard: if Solar API was never called (no raw_data), show a hint
  // banner pointing the operator to "Rigenera rendering" — that
  // re-runs the Creative agent which calls Solar API.
  if (!roof || !potential) {
    return (
      <BentoCard span="full">
        <div className="mb-3">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Solar API
          </p>
          <h2 className="font-headline text-2xl font-bold tracking-tighter">
            Dati Solar API
          </h2>
        </div>
        <div className="space-y-3 rounded-lg bg-surface-container-low p-4 text-sm text-on-surface-variant">
          <p>
            Nessun dato Solar API caricato per questo tetto. Probabilmente la
            pipeline è stata saltata (chiave API mancante, indirizzo non
            geocodificato, oppure render legacy precedente all&apos;adozione
            del flusso AI).
          </p>
          <RegenerateRenderingButton leadId={lead.id} />
        </div>
      </BentoCard>
    );
  }

  const panels = potential.solarPanels ?? [];
  const segments = potential.roofSegmentStats ?? [];
  const maxPanels = potential.maxArrayPanelsCount ?? 0;
  const panelW = potential.panelCapacityWatts ?? 0;
  const dominant = segments.length
    ? segments.reduce((best, curr) =>
        (curr.stats?.areaMeters2 ?? 0) > (best.stats?.areaMeters2 ?? 0)
          ? curr
          : best,
      )
    : null;
  const dominantAz = dominant?.azimuthDegrees;

  return (
    <BentoCard span="full">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Solar API
          </p>
          <h2 className="font-headline text-2xl font-bold tracking-tighter">
            Dati Solar API
          </h2>
          <p className="mt-1 max-w-xl text-sm text-on-surface-variant">
            Output di Google Solar API che ha guidato il preventivo e il
            posizionamento dei pannelli nell&apos;immagine AI. Verifica qui
            prima di inviare l&apos;outreach: se i numeri sono sballati, il
            render è sballato.
          </p>
        </div>
        <RegenerateRenderingButton leadId={lead.id} />
      </div>

      {/* Top-line numbers — same set used by AI paint prompt. */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric
          label="Pannelli (Solar)"
          value={panels.length > 0 ? panels.length : maxPanels}
          hint={panels.length > 0 ? 'panel-list' : 'maxArrayPanelsCount'}
        />
        <Metric
          label="Capienza max"
          value={maxPanels}
          hint="maxArrayPanelsCount"
        />
        <Metric
          label="kWp stimati"
          value={
            roof.estimated_kwp != null
              ? `${formatNumber(roof.estimated_kwp)} kWp`
              : '—'
          }
          hint={panelW ? `${panelW} W/pannello` : undefined}
        />
        <Metric
          label="Producibilità"
          value={
            roof.estimated_yearly_kwh != null
              ? `${formatNumber(roof.estimated_yearly_kwh)} kWh/anno`
              : '—'
          }
        />
        <Metric
          label="Esposizione"
          value={
            dominantAz != null
              ? `${azimuthToCompass(dominantAz)} (${formatNumber(dominantAz)}°)`
              : (roof.exposure ?? '—')
          }
        />
        <Metric
          label="Pendenza"
          value={
            roof.pitch_degrees != null
              ? `${formatNumber(roof.pitch_degrees)}°`
              : '—'
          }
        />
        <Metric
          label="Shading score"
          value={
            roof.shading_score != null
              ? `${formatNumber(roof.shading_score * 100)}%`
              : '—'
          }
          hint="100% = sole pieno"
        />
        <Metric
          label="Superficie tetto"
          value={
            roof.area_sqm != null
              ? `${formatNumber(roof.area_sqm)} m²`
              : '—'
          }
        />
      </div>

      {/* Per-segment breakdown — useful on L-shaped buildings where
          Solar may have picked the wrong wing. */}
      {segments.length > 0 && (
        <div className="mt-6 space-y-2">
          <h3 className="text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
            Segmenti del tetto ({segments.length})
          </h3>
          <div className="overflow-hidden rounded-lg bg-surface-container-low">
            <table className="w-full text-xs">
              <thead className="bg-surface-container">
                <tr className="text-left text-[10px] uppercase tracking-widest text-on-surface-variant">
                  <th className="px-3 py-2 font-semibold">#</th>
                  <th className="px-3 py-2 font-semibold">Esposizione</th>
                  <th className="px-3 py-2 font-semibold">Azimuth</th>
                  <th className="px-3 py-2 font-semibold">Pendenza</th>
                  <th className="px-3 py-2 font-semibold">Area</th>
                  <th className="px-3 py-2 font-semibold">Pannelli</th>
                </tr>
              </thead>
              <tbody>
                {segments.map((seg, idx) => {
                  const az = seg.azimuthDegrees;
                  const area = seg.stats?.areaMeters2;
                  const segPanels = panels.filter(
                    (p) => p.segmentIndex === idx,
                  ).length;
                  const isDominant = seg === dominant;
                  return (
                    <tr
                      key={idx}
                      className={
                        isDominant
                          ? 'bg-primary-container/30 text-on-surface'
                          : 'text-on-surface'
                      }
                    >
                      <td className="px-3 py-2 font-semibold">
                        {idx}
                        {isDominant && (
                          <span className="ml-1 text-[9px] uppercase text-primary">
                            principale
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        {az != null ? azimuthToCompass(az) : '—'}
                      </td>
                      <td className="px-3 py-2">
                        {az != null ? `${formatNumber(az)}°` : '—'}
                      </td>
                      <td className="px-3 py-2">
                        {seg.pitchDegrees != null
                          ? `${formatNumber(seg.pitchDegrees)}°`
                          : '—'}
                      </td>
                      <td className="px-3 py-2">
                        {area != null ? `${formatNumber(area)} m²` : '—'}
                      </td>
                      <td className="px-3 py-2">{segPanels || '—'}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Geo + raw payload (collapsed) — for forensic debugging when
          something looks off and we need to compare with the actual
          API response on disk. */}
      <div className="mt-6 grid gap-3 sm:grid-cols-2">
        {(roof.lat != null || roof.lng != null) && (
          <div className="rounded-lg bg-surface-container-low px-4 py-3 text-xs">
            <p className="font-semibold uppercase tracking-widest text-on-surface-variant">
              Coordinate analizzate
            </p>
            <p className="mt-1 font-mono text-on-surface">
              {roof.lat?.toFixed(6) ?? '—'}, {roof.lng?.toFixed(6) ?? '—'}
            </p>
            {roof.lat != null && roof.lng != null && (
              <a
                className="mt-1 inline-flex items-center gap-1 font-semibold text-primary hover:underline"
                href={`https://www.google.com/maps/@${roof.lat},${roof.lng},20z`}
                target="_blank"
                rel="noreferrer"
              >
                Apri in Google Maps
                <span aria-hidden className="inline-block">
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    width="11"
                    height="11"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.25"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M15 3h6v6" />
                    <path d="m10 14 11-11" />
                    <path d="M21 14v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h6" />
                  </svg>
                </span>
              </a>
            )}
          </div>
        )}
        <details className="rounded-lg bg-surface-container-low px-4 py-3 text-xs">
          <summary className="cursor-pointer font-semibold uppercase tracking-widest text-on-surface-variant">
            Payload Solar API grezzo
          </summary>
          <pre className="mt-2 max-h-64 overflow-auto rounded bg-surface-container-lowest p-2 font-mono text-[10px] leading-relaxed text-on-surface">
            {JSON.stringify(raw, null, 2)}
          </pre>
        </details>
      </div>
    </BentoCard>
  );
}

function Metric({
  label,
  value,
  hint,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
}) {
  return (
    <div className="rounded-lg bg-surface-container-low px-3 py-2.5">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
        {label}
      </p>
      <p className="mt-1 font-headline text-base font-bold tracking-tight text-on-surface">
        {value}
      </p>
      {hint && (
        <p className="mt-0.5 text-[10px] text-on-surface-variant/70">{hint}</p>
      )}
    </div>
  );
}
