/**
 * BrandLogo — renders the real SolarLead logo PNG.
 *
 * Drop the asset at apps/dashboard/public/logo.png.
 * The component renders a plain <img> that inherits its parent's
 * sizing, so callers keep using the `size` prop exactly as before.
 *
 * The PNG has a white background; parent containers should not apply
 * a coloured fill (bg-primary/15 etc.) — use transparent or white.
 */

import { cn } from '@/lib/utils';

interface BrandLogoProps {
  /** Square px size. Defaults to 24. */
  size?: number;
  className?: string;
  /** Optional accessible title / alt text. */
  title?: string;
  /** Kept for API compat — ignored (image is always full-colour). */
  variant?: 'solid' | 'outline';
}

export function BrandLogo({
  size = 24,
  className,
  title,
}: BrandLogoProps) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src="/logo.png"
      alt={title ?? ''}
      aria-hidden={!title}
      width={size}
      height={size}
      className={cn('shrink-0 object-contain', className)}
      style={{ width: size, height: size }}
    />
  );
}
