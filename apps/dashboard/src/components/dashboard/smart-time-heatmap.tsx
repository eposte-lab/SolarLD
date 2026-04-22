/**
 * SmartTimeHeatmap — 7×24 CSS grid showing email-open density by
 * day-of-week × hour (Rome timezone).
 *
 * Pure server component — no client JS. Colour intensity is computed
 * from the normalized open count: 0 = transparent, 1 = primary green.
 *
 * The grid uses CSS custom property --intensity (0-1) so a single
 * Tailwind utility is enough per cell.
 */

import type { HeatmapCell } from '@/lib/data/geo-analytics';

const DOW_LABELS = ['Dom', 'Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab'];

// Hours shown as axis labels (every 4h)
const HOUR_AXIS = [0, 4, 8, 12, 16, 20];

interface SmartTimeHeatmapProps {
  cells: HeatmapCell[];
  className?: string;
}

/** HSL interpolation: transparent → forest green (#006a37) via normalized 0-1. */
function cellStyle(normalized: number): React.CSSProperties {
  if (normalized === 0) {
    return { backgroundColor: 'rgb(170 174 173 / 0.08)' };
  }
  // Low density → light sage, high → forest green, peak → bright green
  const alpha = 0.15 + normalized * 0.85;
  const lightness = 50 - normalized * 25; // 50% (light) → 25% (dark saturated)
  return {
    backgroundColor: `hsla(150, ${Math.round(60 + normalized * 40)}%, ${Math.round(lightness)}%, ${alpha.toFixed(2)})`,
  };
}

export function SmartTimeHeatmap({ cells, className }: SmartTimeHeatmapProps) {
  // Flat map keyed by dow * 24 + hour — avoids noUncheckedIndexedAccess issues
  const normMap = new Map<number, number>();
  let totalOpens = 0;
  let bestDow = 0;
  let bestHour = 9;
  let bestVal = 0;

  for (const cell of cells) {
    normMap.set(cell.dow * 24 + cell.hour, cell.normalized);
    totalOpens += cell.opens;
    if (cell.opens > bestVal) {
      bestVal = cell.opens;
      bestDow = cell.dow;
      bestHour = cell.hour;
    }
  }

  if (cells.length === 0) {
    return (
      <div className={className}>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Orari migliori · Aperture email
        </p>
        <h2 className="font-headline text-2xl font-bold tracking-tighter">
          Smart Time
        </h2>
        <div className="mt-6 flex items-center justify-center rounded-xl bg-surface-container-low py-10">
          <p className="text-sm text-on-surface-variant">
            Nessuna apertura email registrata ancora.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className={className}>
      {/* Header */}
      <div className="mb-4 flex items-end justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Orari migliori · Aperture email
          </p>
          <h2 className="font-headline text-2xl font-bold tracking-tighter">
            Smart Time
          </h2>
        </div>
        {totalOpens > 0 && (
          <div className="text-right">
            <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Slot ottimale
            </p>
            <p className="font-headline text-base font-bold text-primary">
              {DOW_LABELS[bestDow]} {String(bestHour).padStart(2, '0')}:00
            </p>
          </div>
        )}
      </div>

      {/* Grid */}
      <div className="overflow-x-auto">
        <div
          className="min-w-[360px]"
          style={{
            display: 'grid',
            gridTemplateColumns: '28px repeat(24, 1fr)',
            gap: '2px',
          }}
        >
          {/* Hour axis row */}
          <div /> {/* empty corner */}
          {Array.from({ length: 24 }, (_, h) => (
            <div
              key={h}
              className="text-center text-[8px] tabular-nums text-on-surface-variant/60"
            >
              {HOUR_AXIS.includes(h) ? String(h).padStart(2, '0') : ''}
            </div>
          ))}

          {/* DOW rows */}
          {DOW_LABELS.map((label, dow) => (
            <>
              <div
                key={`label-${dow}`}
                className="flex items-center text-[9px] font-semibold text-on-surface-variant"
              >
                {label}
              </div>
              {Array.from({ length: 24 }, (_, hour) => {
                const norm = normMap.get(dow * 24 + hour) ?? 0;
                const isHot = norm > 0.6;
                return (
                  <div
                    key={`${dow}-${hour}`}
                    title={`${DOW_LABELS[dow]} ${String(hour).padStart(2, '0')}:00`}
                    style={cellStyle(norm)}
                    className={`aspect-square rounded-[2px] transition-all duration-300 ${
                      isHot ? 'ring-1 ring-primary/30' : ''
                    }`}
                  />
                );
              })}
            </>
          ))}
        </div>
      </div>

      {/* Colour scale legend */}
      <div className="mt-3 flex items-center gap-2">
        <span className="text-[9px] text-on-surface-variant/60">0</span>
        <div
          className="h-2 flex-1 rounded-full"
          style={{
            background:
              'linear-gradient(to right, rgb(170 174 173 / 0.08), hsl(150 100% 25% / 0.8))',
          }}
        />
        <span className="text-[9px] text-on-surface-variant/60">Più aperture</span>
      </div>
    </div>
  );
}
