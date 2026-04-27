/**
 * Bento unit — the fundamental container of the Editorial Glass
 * system (Sprint 7). Every dashboard page composes these into a grid
 * with a fixed 20px gutter (`gap-5`).
 *
 * Variants:
 *   - default  → solid surface-container-lowest, ghost-border, ambient
 *   - nested   → tinted sub-card inside a default (no border)
 *   - feature  → amber gradient CTA / hero (white text)
 *   - muted    → tonal layer for inline stat strips
 *   - glass    → backdrop-blur-glass over photo / map / dark surface
 *                (signature visual: floats on top of satellite imagery)
 *
 * The `span` prop is a thin sugar over grid-span classes.
 */

import { cn } from '@/lib/utils';

type BentoSpan = '1x1' | '2x1' | '1x2' | '2x2' | '3x1' | 'full';

const SPAN: Record<BentoSpan, string> = {
  '1x1': 'col-span-1 row-span-1',
  '2x1': 'md:col-span-2 row-span-1',
  '1x2': 'col-span-1 row-span-2',
  '2x2': 'md:col-span-2 row-span-2',
  '3x1': 'md:col-span-3 row-span-1',
  full: 'col-span-full',
};

type BentoVariant = 'default' | 'nested' | 'feature' | 'muted' | 'glass';

const VARIANT: Record<BentoVariant, string> = {
  // Default: solid dark card, ghost border, ambient soft glow shadow
  default:
    'bg-surface-container-lowest ghost-border shadow-ambient',
  // Nested: tinted sub-card inside a default — no border, just tonal shift
  nested: 'bg-surface-container-low',
  // Feature: amber gradient hero / CTA card (text-on-primary = near-black)
  feature:
    'bg-gradient-primary text-on-primary shadow-editorial-glow ring-1 ring-white/10',
  // Muted: tonal layer used for inline stat strips
  muted: 'bg-surface-container-low ghost-border',
  // Glass: signature glass card flottante su mappe / foto / hero bg.
  // Bordo gestito dall'inset shadow del .glass-panel utility (vedi globals.css).
  glass: 'glass-panel shadow-ambient',
};

const RADIUS: Record<BentoVariant, string> = {
  default: 'rounded-xl',
  nested: 'rounded-lg',
  feature: 'rounded-xl',
  muted: 'rounded-lg',
  glass: 'rounded-2xl',
};

export interface BentoCardProps extends React.HTMLAttributes<HTMLDivElement> {
  span?: BentoSpan;
  variant?: BentoVariant;
  /** Inner padding. Defaults to 24px; pass `tight` for 16px. */
  padding?: 'tight' | 'default' | 'loose';
}

const PADDING = {
  tight: 'p-4',
  default: 'p-6',
  loose: 'p-8',
} as const;

export function BentoCard({
  span = '1x1',
  variant = 'default',
  padding = 'default',
  className,
  children,
  ...rest
}: BentoCardProps) {
  return (
    <div
      className={cn(
        RADIUS[variant],
        VARIANT[variant],
        PADDING[padding],
        SPAN[span],
        'transition-all duration-200',
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

/**
 * `BentoGrid` — convenience wrapper with the 20px gutter baked in.
 *
 * Most pages use 4 columns on desktop; pass `cols={3}` or `cols={6}`
 * to override for denser layouts.
 */
export function BentoGrid({
  cols = 4,
  className,
  children,
}: {
  cols?: 2 | 3 | 4 | 5 | 6;
  className?: string;
  children: React.ReactNode;
}) {
  const colClass = {
    2: 'md:grid-cols-2',
    3: 'md:grid-cols-3',
    4: 'md:grid-cols-4',
    5: 'md:grid-cols-5',
    6: 'md:grid-cols-6',
  }[cols];

  return (
    <div
      className={cn('grid grid-cols-1 gap-5', colClass, className)}
    >
      {children}
    </div>
  );
}
