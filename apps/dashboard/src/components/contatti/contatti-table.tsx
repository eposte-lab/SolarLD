'use client';

/**
 * ContattiTable — client wrapper around the contatti list table.
 *
 * Owned by the server component at `app/(dashboard)/contatti/page.tsx`,
 * which prefetches v3-qualified `rows` (solar_verdict='accepted') and
 * passes them in. Sort works within the current paginated page only.
 *
 * Display priority: v3 enrichment fields (Google Places + scraping +
 * Haiku scoring) with legacy v2 (Atoka) columns as fallback. The
 * resolution helpers live in `lib/data/contatti.ts`.
 */

import { SortableTh } from '@/components/ui/sortable-th';
import { useSortableData } from '@/hooks/use-sortable-data';
import { sectorLabel } from '@/lib/sector-labels';
import { cn, relativeTime } from '@/lib/utils';
import {
  displayCity,
  displayEmail,
  displayName,
  displayOverallScore,
  displayPhone,
  displayProvince,
  type ContattoRow,
} from '@/lib/contatti-display';

const VERDICT_STYLES: Record<string, string> = {
  accepted: 'bg-primary-container text-on-primary-container',
  rejected_tech: 'bg-secondary-container text-on-secondary-container',
  no_building: 'bg-surface-container-highest text-on-surface-variant',
  api_error: 'bg-surface-container-highest text-on-surface-variant',
  skipped_below_gate: 'bg-surface-container text-on-surface-variant opacity-70',
};

const VERDICT_LABELS: Record<string, string> = {
  accepted: 'Tetto idoneo',
  rejected_tech: 'Rifiutato (tecnico)',
  no_building: 'Nessun edificio',
  api_error: 'Errore API',
  skipped_below_gate: 'Skip (gate)',
};

const VERDICT_ORDER: Record<string, number> = {
  accepted: 0,
  rejected_tech: 1,
  no_building: 2,
  api_error: 3,
  skipped_below_gate: 4,
};

type SortKey =
  | 'name'
  | 'sector'
  | 'comune'
  | 'score'
  | 'quality'
  | 'verdict'
  | 'contact'
  | 'created';

export function ContattiTable({ rows }: { rows: ContattoRow[] }) {
  const { sorted, sortKey, sortDir, requestSort } = useSortableData<
    ContattoRow,
    SortKey
  >(rows, (c, key) => {
    switch (key) {
      case 'name':
        return displayName(c);
      case 'sector':
        return c.predicted_sector ?? '';
      case 'comune':
        return displayCity(c) ?? '';
      case 'score':
        return displayOverallScore(c) ?? null;
      case 'quality':
        return c.building_quality_score ?? null;
      case 'verdict':
        return c.solar_verdict ? VERDICT_ORDER[c.solar_verdict] ?? 99 : null;
      case 'contact':
        return displayEmail(c) ?? displayPhone(c) ?? '';
      case 'created':
        return c.created_at;
    }
  });

  return (
    <div className="overflow-hidden rounded-lg bg-surface-container-low">
      <table className="w-full text-sm">
        <thead>
          <tr>
            <SortableTh sortKey="name" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Azienda</SortableTh>
            <SortableTh sortKey="sector" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Settore</SortableTh>
            <SortableTh sortKey="comune" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Comune</SortableTh>
            <SortableTh sortKey="score" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3" align="right">Score</SortableTh>
            <SortableTh sortKey="quality" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3" align="center">Qualità</SortableTh>
            <SortableTh sortKey="verdict" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Solar</SortableTh>
            <SortableTh sortKey="contact" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Contatto</SortableTh>
            <SortableTh sortKey="created" active={sortKey} dir={sortDir} onSort={requestSort} className="px-5 py-3">Scan</SortableTh>
          </tr>
        </thead>
        <tbody className="bg-surface-container-lowest">
          {sorted.map((c, idx) => {
            const name = displayName(c);
            const city = displayCity(c);
            const province = displayProvince(c);
            const score = displayOverallScore(c);
            const email = displayEmail(c);
            const phone = displayPhone(c);
            return (
              <tr
                key={c.id}
                className="transition-colors hover:bg-surface-container-low"
                style={
                  idx !== 0
                    ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }
                    : undefined
                }
              >
                {/* Azienda */}
                <td className="px-5 py-4">
                  <div className="font-semibold text-on-surface">{name}</div>
                  {c.vat_number ? (
                    <div className="font-mono text-[10px] text-on-surface-variant">
                      {c.vat_number}
                    </div>
                  ) : null}
                </td>

                {/* Settore */}
                <td className="px-5 py-4 text-on-surface-variant">
                  {sectorLabel(c.predicted_sector)}
                </td>

                {/* Comune */}
                <td className="px-5 py-4 text-on-surface-variant">
                  {city ?? '—'}{' '}
                  {province ? (
                    <span className="text-[10px] font-semibold uppercase opacity-60">
                      ({province})
                    </span>
                  ) : null}
                </td>

                {/* Score */}
                <td className="px-5 py-4 text-right font-headline font-bold tabular-nums">
                  {score != null ? (
                    <span
                      className={cn(
                        score >= 75
                          ? 'text-success'
                          : score >= 60
                            ? 'text-warning'
                            : 'text-on-surface',
                      )}
                    >
                      {score}
                    </span>
                  ) : (
                    <span className="text-on-surface-variant">—</span>
                  )}
                </td>

                {/* Qualità edificio */}
                <td className="px-5 py-4 text-center tabular-nums text-on-surface-variant">
                  {c.building_quality_score != null
                    ? `${c.building_quality_score}/5`
                    : '—'}
                </td>

                {/* Verdetto Solar */}
                <td className="px-5 py-4">
                  {c.solar_verdict ? (
                    <span
                      className={cn(
                        'inline-flex rounded-md px-2 py-0.5 text-xs font-medium',
                        VERDICT_STYLES[c.solar_verdict] ??
                          'bg-surface-container text-on-surface-variant',
                      )}
                    >
                      {VERDICT_LABELS[c.solar_verdict] ?? c.solar_verdict}
                    </span>
                  ) : (
                    <span className="text-xs text-on-surface-variant">—</span>
                  )}
                </td>

                {/* Contatto: email + telefono */}
                <td className="px-5 py-4 text-xs">
                  {email ? (
                    <a
                      href={`mailto:${email}`}
                      className="block text-primary hover:underline"
                    >
                      {email}
                    </a>
                  ) : null}
                  {phone ? (
                    <a
                      href={`tel:${phone}`}
                      className="block text-on-surface-variant hover:text-on-surface"
                    >
                      {phone}
                    </a>
                  ) : null}
                  {!email && !phone ? (
                    <span className="text-on-surface-variant">—</span>
                  ) : null}
                </td>

                {/* Scan */}
                <td className="px-5 py-4 text-xs text-on-surface-variant">
                  {relativeTime(c.created_at)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
