/**
 * GlassPanel — floating glassmorphic surface.
 *
 * Per DESIGN.md §2: use for overlays (map tooltips, mobile nav,
 * lead-detail action rail). Never for a background region.
 *
 *   Fill:  surface-container-lowest @ 70% opacity
 *   Blur:  24px backdrop-filter
 */

import { cn } from '@/lib/utils';

export interface GlassPanelProps extends React.HTMLAttributes<HTMLDivElement> {
  /** Corner radius. Defaults to `xl`. */
  radius?: 'lg' | 'xl' | 'full';
}

const RADIUS = {
  lg: 'rounded-lg',
  xl: 'rounded-xl',
  full: 'rounded-full',
} as const;

export function GlassPanel({
  radius = 'xl',
  className,
  children,
  ...rest
}: GlassPanelProps) {
  return (
    <div
      className={cn(
        'glass-panel ghost-border shadow-ambient',
        RADIUS[radius],
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}
