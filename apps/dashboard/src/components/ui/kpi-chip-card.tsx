/**
 * KPI chip card — Editorial Glass (Sprint 7).
 *
 * Two sizes:
 *   - default → compact tile per le KPI strip standard
 *   - hero    → numero gigante con composizione "big + dimmed decimal"
 *               (es. "78.3%" → "78" full + ".3%" @60% opacità)
 *
 * Single-accent system: l'unico highlight cromatico è amber. I vecchi
 * accent "primary green / tertiary gold / secondary terracotta" sono
 * mappati allo stesso amber per backward-compat — la scelta semantica
 * vera vive nel nuovo prop `tone`:
 *
 *   - 'neutral'    → no stripe, solo grigi
 *   - 'highlight'  → stripe + numero in amber (per metriche che richiedono attenzione)
 *   - 'success'    → stripe verde desaturato (won, online, healthy)
 *   - 'critical'   → stripe rosso desaturato (alarm)
 */

import { cn } from '@/lib/utils';

// Legacy: i 4 ruoli MD3. Mantenuti per non rompere call-sites,
// rimappati semanticamente sui nuovi tone.
type KpiAccent = 'primary' | 'tertiary' | 'secondary' | 'neutral';

type KpiTone = 'neutral' | 'highlight' | 'success' | 'critical';
type KpiSize = 'default' | 'hero';

const TONE_STRIPE: Record<KpiTone, string> = {
  neutral: 'before:bg-white/8',
  highlight: 'before:bg-primary',
  success: 'before:bg-success',
  critical: 'before:bg-error',
};

const TONE_TEXT: Record<KpiTone, string> = {
  neutral: 'text-on-surface',
  highlight: 'text-primary',
  success: 'text-success',
  critical: 'text-error',
};

// Legacy `accent` → new `tone` rimapping
const ACCENT_TO_TONE: Record<KpiAccent, KpiTone> = {
  primary: 'highlight', // legacy primary green → amber highlight
  tertiary: 'highlight', // legacy gold → amber highlight (same single accent)
  secondary: 'critical', // legacy terracotta urgency → critical red
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
        'relative overflow-hidden rounded-xl bg-surface-container-lowest ghost-border shadow-ambient',
        isHero ? 'p-7' : 'p-6',
        // Top accent stripe
        'before:absolute before:left-0 before:right-0 before:top-0 before:h-1',
        TONE_STRIPE[effectiveTone],
        className,
      )}
    >
      <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
        {label}
      </p>
      <p
        className={cn(
          'mt-3 font-headline font-bold leading-none tracking-tightest tabular-nums',
          isHero ? 'text-6xl md:text-7xl animate-numeric-reveal' : 'text-4xl tracking-tighter',
          TONE_TEXT[effectiveTone],
          effectiveTone === 'highlight' && isHero && 'editorial-glow',
        )}
      >
        <span>{mainPart}</span>
        {decimalPart && <span className="hero-decimal">{decimalPart}</span>}
      </p>
      <div className="mt-3 flex items-center gap-2">
        {trend !== undefined && (
          <span
            className={cn(
              'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold tabular-nums',
              trend.delta > 0
                ? 'bg-success-container text-on-success-container'
                : trend.delta < 0
                  ? 'bg-error-container text-on-error-container'
                  : 'bg-surface-container-high text-on-surface-variant',
            )}
          >
            <span aria-hidden>
              {trend.delta > 0 ? '↑' : trend.delta < 0 ? '↓' : '→'}
            </span>
            {formatDelta(trend.delta, trend.unit)}
          </span>
        )}
        {hint && <p className="text-xs text-on-surface-variant">{hint}</p>}
      </div>
    </div>
  );
}
