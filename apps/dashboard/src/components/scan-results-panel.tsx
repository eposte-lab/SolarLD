/**
 * Scan results panel for the /territorio page.
 *
 * Shows:
 *   1. Funnel waterfall (L1→L5 stage counts with drop-off %)
 *   2. Cost badge (total EUR for the last scan)
 *   3. Top recommended candidates table (recommended_for_rendering=true)
 *
 * Server component — rendered on page load/refresh after the funnel job
 * completes. No polling (operator refreshes manually).
 */

import type { ScanResultsResponse, ScanStageSummary } from '@/lib/data/territory';

const SECTOR_LABELS: Record<string, string> = {
  industry_heavy: 'Manifatturiero pesante',
  industry_light: 'Manifatturiero leggero',
  food_production: 'Produzione alimentare',
  logistics: 'Logistica',
  retail_gdo: 'Grande distribuzione',
  hospitality_large: 'Ricettivo grande',
  hospitality_food_service: 'Ristorazione collettiva',
  healthcare: 'Sanitario',
  agricultural_intensive: 'Agricolo intensivo',
  automotive: 'Automotive',
  education: 'Istruzione',
  personal_services: 'Servizi alla persona',
  professional_offices: 'Studi professionali',
  horeca: 'HoReCa',
};

const SOLAR_VERDICT_LABELS: Record<string, string> = {
  accepted: '✅ Solar OK',
  rejected_tech: '❌ Tech fail',
  no_building: '⬜ No edificio',
  api_error: '⚠️ API error',
  skipped_below_gate: '⏭ Skip (gate)',
};

interface WaterfallStepProps {
  label: string;
  count: number;
  prevCount: number | null;
  color: string;
}

function WaterfallStep({ label, count, prevCount, color }: WaterfallStepProps) {
  const dropPct =
    prevCount != null && prevCount > 0
      ? Math.round((1 - count / prevCount) * 100)
      : null;

  return (
    <div className="flex items-center gap-3">
      <div className="w-32 shrink-0 text-right text-xs text-on-surface-variant">
        {label}
      </div>
      <div className="relative flex-1">
        <div
          className={`h-6 rounded-sm ${color} transition-all`}
          style={{
            width: `${prevCount ? Math.max(4, (count / prevCount) * 100) : 100}%`,
          }}
        />
        <span className="absolute inset-0 flex items-center pl-2 text-xs font-semibold text-on-primary">
          {count.toLocaleString('it-IT')}
        </span>
      </div>
      {dropPct !== null ? (
        <div className="w-16 shrink-0 text-xs text-on-surface-variant">
          −{dropPct}%
        </div>
      ) : (
        <div className="w-16" />
      )}
    </div>
  );
}

function SummaryWaterfall({ s }: { s: ScanStageSummary }) {
  const steps = [
    { label: 'L1 Places', count: s.l1_candidates, color: 'bg-primary' },
    { label: 'L2 Con email', count: s.l2_with_email, color: 'bg-primary/80' },
    { label: 'L3 Qualità ≥3', count: s.l3_accepted, color: 'bg-primary/70' },
    { label: 'L4 Solar OK', count: s.l4_solar_accepted, color: 'bg-secondary' },
    { label: 'L5 Top score', count: s.l5_recommended, color: 'bg-tertiary' },
  ];

  return (
    <div className="space-y-2">
      {steps.map((step, i) => (
        <WaterfallStep
          key={step.label}
          label={step.label}
          count={step.count}
          prevCount={i === 0 ? null : (steps[i - 1]?.count ?? null)}
          color={step.color}
        />
      ))}
    </div>
  );
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('it-IT', {
    day: '2-digit',
    month: 'short',
    hour: '2-digit',
    minute: '2-digit',
  });
}

interface Props {
  data: ScanResultsResponse;
}

export function ScanResultsPanel({ data }: Props) {
  const { summary: s, top_candidates } = data;

  const isEmpty = s.l1_candidates === 0;

  return (
    <div className="space-y-6">
      {/* ---- Waterfall + cost ---- */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {/* Funnel waterfall */}
        <div className="col-span-2 rounded-md border border-outline-variant bg-surface-container p-4">
          <div className="mb-3 flex items-center justify-between">
            <p className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              Funnel v3 — ultima scansione
            </p>
            {s.started_at ? (
              <span className="text-xs text-on-surface-variant">
                {fmtDate(s.started_at)}
              </span>
            ) : null}
          </div>
          {isEmpty ? (
            <p className="text-sm text-on-surface-variant">
              Nessuna scansione ancora. Premi{' '}
              <strong>Avvia scansione v3</strong> per iniziare.
            </p>
          ) : (
            <SummaryWaterfall s={s} />
          )}
        </div>

        {/* Cost + timing */}
        <div className="flex flex-col gap-3 rounded-md border border-outline-variant bg-surface-container p-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
            Costi ultima scansione
          </p>
          <p className="text-3xl font-bold tabular-nums text-on-surface">
            €{s.total_cost_eur.toFixed(2)}
          </p>
          <div className="space-y-1 text-xs text-on-surface-variant">
            <p>Avvio: {fmtDate(s.started_at)}</p>
            <p>Fine: {fmtDate(s.completed_at)}</p>
          </div>
          {s.l5_recommended > 0 ? (
            <div className="rounded-md bg-tertiary/10 px-3 py-2 text-xs text-tertiary">
              <span className="font-bold">{s.l5_recommended}</span> candidati
              raccomandati per rendering
            </div>
          ) : null}
        </div>
      </div>

      {/* ---- Top candidates ---- */}
      {top_candidates.length > 0 ? (
        <div className="space-y-3">
          <h3 className="text-base font-semibold text-on-surface">
            Candidati raccomandati ({top_candidates.length})
          </h3>
          <div className="overflow-x-auto rounded-md border border-outline-variant">
            <table className="min-w-full divide-y divide-outline-variant text-sm">
              <thead className="bg-surface-container-high text-xs uppercase tracking-wider text-on-surface-variant">
                <tr>
                  <th className="px-3 py-2 text-left">Azienda</th>
                  <th className="px-3 py-2 text-left">Settore</th>
                  <th className="px-3 py-2 text-center">Score</th>
                  <th className="px-3 py-2 text-center">Qualità</th>
                  <th className="px-3 py-2 text-center">Solar</th>
                  <th className="px-3 py-2 text-left">Contatto</th>
                  <th className="px-3 py-2 text-left">Maps</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-outline-variant bg-surface-container">
                {top_candidates.map((c) => {
                  const mapsUrl =
                    c.lat && c.lng
                      ? `https://www.google.com/maps?q=${c.lat},${c.lng}`
                      : null;
                  const scoreColor =
                    (c.overall_score ?? 0) >= 75
                      ? 'text-success font-bold'
                      : (c.overall_score ?? 0) >= 60
                        ? 'text-warning font-semibold'
                        : 'text-on-surface';
                  return (
                    <tr
                      key={c.id}
                      className="hover:bg-surface-container-high"
                    >
                      <td className="px-3 py-2 font-medium text-on-surface">
                        {c.business_name ?? '—'}
                        {c.website ? (
                          <a
                            href={c.website}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="ml-1.5 text-xs text-primary hover:underline"
                          >
                            ↗
                          </a>
                        ) : null}
                      </td>
                      <td className="px-3 py-2 text-xs text-on-surface-variant">
                        {c.predicted_sector
                          ? (SECTOR_LABELS[c.predicted_sector] ?? c.predicted_sector)
                          : '—'}
                      </td>
                      <td className={`px-3 py-2 text-center tabular-nums ${scoreColor}`}>
                        {c.overall_score != null ? `${c.overall_score}/100` : '—'}
                      </td>
                      <td className="px-3 py-2 text-center tabular-nums text-on-surface">
                        {c.building_quality_score != null
                          ? `${c.building_quality_score}/5`
                          : '—'}
                      </td>
                      <td className="px-3 py-2 text-center text-xs">
                        {c.solar_verdict
                          ? (SOLAR_VERDICT_LABELS[c.solar_verdict] ?? c.solar_verdict)
                          : '—'}
                      </td>
                      <td className="px-3 py-2 text-xs text-on-surface-variant">
                        {c.best_email ? (
                          <a
                            href={`mailto:${c.best_email}`}
                            className="text-primary hover:underline"
                          >
                            {c.best_email}
                          </a>
                        ) : c.phone ? (
                          c.phone
                        ) : (
                          '—'
                        )}
                      </td>
                      <td className="px-3 py-2 text-xs">
                        {mapsUrl ? (
                          <a
                            href={mapsUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-primary hover:underline"
                          >
                            {c.lat?.toFixed(4)}, {c.lng?.toFixed(4)}
                          </a>
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : !isEmpty ? (
        <div className="rounded-md border border-outline-variant bg-surface-container/50 p-4 text-sm text-on-surface-variant">
          Nessun candidato raccomandato ancora. Il funnel ha processato{' '}
          {s.l1_candidates} candidati ma nessuno ha superato la soglia score
          ≥ 60 per il rendering.
        </div>
      ) : null}
    </div>
  );
}
