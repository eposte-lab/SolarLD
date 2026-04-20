/**
 * KPI chip card — the small metric tiles used on the Overview page.
 *
 * Replaces the legacy `StatCard`. Visual spec (DESIGN.md §2 + §5):
 *   - White surface, `xl` corner, ambient shadow
 *   - `label-sm` all-caps label in on-surface-variant
 *   - Big `headline-lg` (Plus Jakarta Sans, tracking -2%) numeric value
 *   - Optional trend chip (up/down arrow + delta) using secondary-container
 *     for positive, surface-container-high for neutral
 *   - Optional accent stripe along the top via `accent` prop
 */

import { cn } from '@/lib/utils';

type KpiAccent = 'primary' | 'tertiary' | 'secondary' | 'neutral';

const ACCENT_STRIPE: Record<KpiAccent, string> = {
  primary: 'before:bg-primary',
  tertiary: 'before:bg-tertiary-container',
  secondary: 'before:bg-secondary-container',
  neutral: 'before:bg-surface-container-high',
};

const ACCENT_TEXT: Record<KpiAccent, string> = {
  primary: 'text-primary',
  tertiary: 'text-tertiary',
  secondary: 'text-secondary',
  neutral: 'text-on-surface',
};

export interface KpiChipCardProps {
  label: string;
  value: React.ReactNode;
  /** Optional sub-line — relative period, unit, tenant etc. */
  hint?: string;
  /** Trend delta. Positive = up arrow + tertiary-container chip. */
  trend?: { delta: number; unit?: string };
  accent?: KpiAccent;
  className?: string;
}

function formatDelta(delta: number, unit: string | undefined): string {
  const sign = delta > 0 ? '+' : delta < 0 ? '' : '±';
  return `${sign}${delta.toFixed(1)}${unit ?? '%'}`;
}

export function KpiChipCard({
  label,
  value,
  hint,
  trend,
  accent = 'neutral',
  className,
}: KpiChipCardProps) {
  return (
    <div
      className={cn(
        // Surface layer + ambient shadow
        'relative overflow-hidden rounded-xl bg-surface-container-lowest p-6 shadow-ambient',
        // Accent top stripe — pseudo-element so it sits inside the radius
        'before:absolute before:left-0 before:right-0 before:top-0 before:h-1',
        ACCENT_STRIPE[accent],
        className,
      )}
    >
      <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
        {label}
      </p>
      <p
        className={cn(
          'mt-3 font-headline text-4xl font-bold leading-none tracking-tighter',
          ACCENT_TEXT[accent],
        )}
      >
        {value}
      </p>
      <div className="mt-3 flex items-center gap-2">
        {trend !== undefined && (
          <span
            className={cn(
              'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold',
              trend.delta > 0
                ? 'bg-primary-container text-on-primary-container'
                : trend.delta < 0
                  ? 'bg-secondary-container text-on-secondary-container'
                  : 'bg-surface-container-high text-on-surface-variant',
            )}
          >
            <span aria-hidden>
              {trend.delta > 0 ? '↑' : trend.delta < 0 ? '↓' : '→'}
            </span>
            {formatDelta(trend.delta, trend.unit)}
          </span>
        )}
        {hint && (
          <p className="text-xs text-on-surface-variant">{hint}</p>
        )}
      </div>
    </div>
  );
}
