/**
 * GlassPanel — floating glassmorphic surface (Editorial Glass).
 *
 * Use for floating overlays (map tooltips, hero info-chip, mobile nav,
 * lead-detail action rail). Never as a background region.
 *
 *   sm  → 16px blur, used for compact pills / inline chips
 *   md  → 28px blur (default), standard card overlay
 *   lg  → 40px blur, hero card flottante su mappe satellite/foto
 */

import { cn } from '@/lib/utils';

export interface GlassPanelProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Blur intensity. Defaults to `md`. */
  blur?: 'sm' | 'md' | 'lg';
  /** Corner radius. Defaults to `xl`. */
  radius?: 'lg' | 'xl' | '2xl' | 'full';
}

const BLUR = {
  sm: 'glass-panel-sm',
  md: 'glass-panel',
  lg: 'glass-panel-lg',
} as const;

const RADIUS = {
  lg: 'rounded-lg',
  xl: 'rounded-xl',
  '2xl': 'rounded-2xl',
  full: 'rounded-full',
} as const;

export function GlassPanel({
  blur = 'md',
  radius = 'xl',
  className,
  children,
  ...rest
}: GlassPanelProps) {
  return (
    <div
      className={cn(BLUR[blur], RADIUS[radius], 'shadow-ambient', className)}
      {...rest}
    >
      {children}
    </div>
  );
}
