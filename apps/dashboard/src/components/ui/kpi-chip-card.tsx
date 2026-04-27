/**
 * KPI chip card — Liquid Glass (V2).
 *
 * Two sizes:
 *   - default → compact tile per le KPI strip standard
 *   - hero    → numero gigante con composizione "big + dimmed decimal"
 *
 * Single-accent system: l'unico highlight cromatico è mint. La scelta
 * semantica vive nel prop `tone`:
 *
 *   - 'neutral'    → no stripe, solo grigi
 *   - 'highlight'  → stripe + numero in mint (metriche positive)
 *   - 'success'    → stripe mint (won, online, healthy) — alias di highlight
 *   - 'critical'   → stripe rosso desaturato (alarm)
 *   - 'warning'    → stripe amber (rare: warm-up, cap reached)
 */

import { cn } from '@/lib/utils';

// Legacy: i 4 ruoli MD3. Mantenuti per non rompere call-sites,
// rimappati semanticamente sui nuovi tone.
type KpiAccent = 'primary' | 'tertiary' | 'secondary' | 'neutral';

type KpiTone = 'neutral' | 'highlight' | 'success' | 'critical' | 'warning';
type KpiSize = 'default' | 'hero';

const TONE_STRIPE: Record<KpiTone, string> = {
  neutral: 'before:bg-white/10',
  highlight: 'before:bg-primary',
  success: 'before:bg-primary',
  critical: 'before:bg-error',
  warning: 'before:bg-warning',
};

const TONE_TEXT: Record<KpiTone, string> = {
  neutral: 'text-on-surface',
  highlight: 'text-primary',
  success: 'text-primary',
  critical: 'text-error',
  warning: 'text-warning',
};

// Legacy `accent` → new `tone` rimapping
const ACCENT_TO_TONE: Record<KpiAccent, KpiTone> = {
  primary: 'highlight',
  tertiary: 'highlight',
  secondary: 'critical',
  neutral: 'neutral',
};

export interface KpiChipCardProps {
  label: string;
  value: React.ReactNode;
  /** Optional sub-line — relative period, unit, tenant etc. */
  hint?: string;
  /** Trend delta. Positive = up arrow chip. */
  trend?: { delta: number; unit?: string };
  /** Size variant. `hero` enlarges value to text-6xl/7xl with reveal anim. */
  size?: KpiSize;
  /** Semantic tone (preferred over legacy `accent`). */
  tone?: KpiTone;
  /** Legacy. Mapped to `tone` if provided. */
  accent?: KpiAccent;
  className?: string;
}

function formatDelta(delta: number, unit: string | undefined): string {
  const sign = delta > 0 ? '+' : delta < 0 ? '' : '±';
  return `${sign}${delta.toFixed(1)}${unit ?? '%'}`;
}

/**
 * Splits "78.3%" into ["78", ".3%"] so the decimal portion can be
 * rendered with `.hero-decimal` (lighter weight, dimmed opacity).
 * Falls back gracefully for non-numeric values.
 */
function splitHeroValue(
  value: React.ReactNode,
): [React.ReactNode, React.ReactNode | null] {
  if (typeof value !== 'string') return [value, null];
  const match = value.match(/^([+-]?\d{1,3}(?:[.,]\d+)?(?:k|m|b)?)(.+)?$/i);
  if (!match) return [value, null];
  const num = match[1] ?? '';
  const suffix = match[2];
  // Split decimal: "78.3" → ["78", ".3"]
  const decMatch = num.match(/^([+-]?\d+)([.,]\d+)?$/);
  if (decMatch && decMatch[2]) {
    return [decMatch[1], `${decMatch[2]}${suffix ?? ''}`];
  }
  return [num, suffix ?? null];
}

export function KpiChipCard({
  label,
  value,
  hint,
  trend,
  size = 'default',
  tone,
  accent,
  className,
}: KpiChipCardProps) {
  // Resolve effective tone: explicit `tone` wins, else map from legacy `accent`
  const effectiveTone: KpiTone = tone ?? (accent ? ACCENT_TO_TONE[accent] : 'neutral');

  const isHero = size === 'hero';
  const [mainPart, decimalPart] = isHero ? splitHeroValue(value) : [value, null];

  return (
    <div
      className={cn(
        'group relative overflow-hidden rounded-2xl liquid-glass-sm transition-all duration-300 hover:-translate-y-0.5 hover:shadow-liquid-glass',
        isHero ? 'p-7' : 'p-5',
        // Top accent stripe
        'before:absolute before:left-0 before:right-0 before:top-0 before:h-[3px]',
        'before:rounded-t-2xl',
        TONE_STRIPE[effectiveTone],
        className,
      )}
    >
      {/* Specular top-edge highlight */}
      <span
        className="pointer-events-none absolute inset-x-0 top-0 h-[40%] bg-glass-specular"
        aria-hidden
      />
      <div className="relative">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-on-surface-variant">
          {label}
        </p>
        <p
          className={cn(
            'mt-3 font-headline font-bold leading-none tracking-tightest tabular-nums',
            isHero
              ? 'text-6xl md:text-7xl animate-numeric-reveal'
              : 'text-[2.25rem] tracking-tighter',
            TONE_TEXT[effectiveTone],
            (effectiveTone === 'highlight' || effectiveTone === 'success') &&
              isHero &&
              'editorial-glow',
          )}
        >
          <span>{mainPart}</span>
          {decimalPart && <span className="hero-decimal">{decimalPart}</span>}
        </p>
        <div className="mt-3 flex items-center gap-2">
          {trend !== undefined && (
            <TrendChip delta={trend.delta} unit={trend.unit} />
          )}
          {hint && (
            <p className="text-[11px] text-on-surface-variant">{hint}</p>
          )}
        </div>
      </div>
    </div>
  );
}

function TrendChip({ delta, unit }: { delta: number; unit?: string }) {
  const positive = delta > 0;
  const negative = delta < 0;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10.5px] font-semibold tabular-nums',
        positive
          ? 'bg-primary/15 text-primary'
          : negative
            ? 'bg-error/15 text-error'
            : 'bg-white/[0.06] text-on-surface-variant',
      )}
    >
      <ArrowGlyph direction={positive ? 'up' : negative ? 'down' : 'flat'} />
      {formatDelta(delta, unit)}
    </span>
  );
}

function ArrowGlyph({ direction }: { direction: 'up' | 'down' | 'flat' }) {
  // Inline SVG triangoli — no unicode arrow, sempre crisp.
  if (direction === 'up') {
    return (
      <svg width="8" height="8" viewBox="0 0 8 8" fill="currentColor" aria-hidden>
        <path d="M4 1L7 6.5H1L4 1Z" />
      </svg>
    );
  }
  if (direction === 'down') {
    return (
      <svg width="8" height="8" viewBox="0 0 8 8" fill="currentColor" aria-hidden>
        <path d="M4 7L1 1.5H7L4 7Z" />
      </svg>
    );
  }
  return (
    <svg width="8" height="8" viewBox="0 0 8 8" fill="currentColor" aria-hidden>
      <rect x="1" y="3.25" width="6" height="1.5" rx="0.75" />
    </svg>
  );
}
