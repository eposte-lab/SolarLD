/**
 * BrandLogo — SolarLead's custom monogram.
 *
 * Concept "Photovoltaic Lead":
 *   - A rounded square evokes a photovoltaic module/cell.
 *   - The top-left quadrant is filled (the "energized" cell) — the
 *     converted lead.
 *   - A small disc in the top-right corner is the inbound signal —
 *     the new lead arriving on the panel.
 *   - Subtle inner grid divider lines reinforce the panel reading.
 *
 * Designed to read well at every used size (16px chip, 20px sidebar
 * monogram, 32px hero badge, 56px+ on auth screens). Uses
 * `currentColor`, so the parent decides the tone (mint primary by
 * default, white-on-dark in compact chrome contexts).
 *
 * The mark is intentionally minimal: it has to coexist next to a
 * wordmark in the brand lockup and not fight typography.
 */

import { cn } from '@/lib/utils';

interface BrandLogoProps {
  /** Square px size. Defaults to 24. */
  size?: number;
  /** Render style. `solid` = full-color glyph for compact chrome.
   *  `outline` = lighter stroke variant for hero / display contexts. */
  variant?: 'solid' | 'outline';
  className?: string;
  /** Optional accessible title; if omitted, marked aria-hidden. */
  title?: string;
}

export function BrandLogo({
  size = 24,
  variant = 'solid',
  className,
  title,
}: BrandLogoProps) {
  const labelled = !!title;
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 28 28"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      role={labelled ? 'img' : undefined}
      aria-hidden={labelled ? undefined : true}
      aria-label={labelled ? title : undefined}
      className={cn('shrink-0', className)}
    >
      {labelled && <title>{title}</title>}

      {/* Outer panel frame */}
      <rect
        x="3"
        y="3"
        width="22"
        height="22"
        rx="6.25"
        stroke="currentColor"
        strokeWidth={variant === 'outline' ? 1.4 : 1.6}
        strokeOpacity={variant === 'outline' ? 0.85 : 1}
      />

      {/* Subtle 2×2 grid dividers */}
      <line
        x1="14"
        y1="5"
        x2="14"
        y2="23"
        stroke="currentColor"
        strokeWidth="1"
        strokeOpacity="0.32"
        strokeLinecap="round"
      />
      <line
        x1="5"
        y1="14"
        x2="23"
        y2="14"
        stroke="currentColor"
        strokeWidth="1"
        strokeOpacity="0.32"
        strokeLinecap="round"
      />

      {/* Energized top-left cell — the "converted" lead */}
      <rect
        x="6.5"
        y="6.5"
        width="6"
        height="6"
        rx="1.6"
        fill="currentColor"
        fillOpacity={variant === 'outline' ? 0.85 : 1}
      />

      {/* Lead signal — the inbound dot in the top-right cell */}
      <circle
        cx="19.5"
        cy="8.5"
        r="1.65"
        fill="currentColor"
        fillOpacity={variant === 'outline' ? 0.95 : 1}
      />

      {/* Soft halo around the signal dot — only on solid variant */}
      {variant === 'solid' && (
        <circle
          cx="19.5"
          cy="8.5"
          r="3.2"
          stroke="currentColor"
          strokeWidth="0.9"
          strokeOpacity="0.35"
        />
      )}
    </svg>
  );
}
