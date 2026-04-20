/**
 * Inline SVG sparkline — zero-dependency, server-renderable.
 *
 * For the analytics page we only need a compact shape showing the
 * trend of daily spend. A full `recharts` setup would force the
 * whole page to "use client"; since the data is static per request
 * this SVG path is both lighter and equally expressive.
 */

import { cn } from '@/lib/utils';

export interface SparklineProps {
  /** Y-axis values, left-to-right. */
  values: number[];
  /** Width in px. Defaults to 240. */
  width?: number;
  /** Height in px. Defaults to 56. */
  height?: number;
  /** Stroke colour. Defaults to currentColor. */
  stroke?: string;
  /** Fill under the curve — light tint, often translucent. */
  fill?: string;
  className?: string;
  ariaLabel?: string;
}

export function Sparkline({
  values,
  width = 240,
  height = 56,
  stroke = 'currentColor',
  fill = 'currentColor',
  className,
  ariaLabel,
}: SparklineProps) {
  if (values.length === 0) {
    return (
      <div
        className={cn(
          'flex items-center justify-center text-xs text-on-surface-variant',
          className,
        )}
        style={{ width, height }}
      >
        —
      </div>
    );
  }

  const max = Math.max(...values, 1);
  const min = Math.min(...values, 0);
  const range = Math.max(max - min, 1);

  const stepX = values.length > 1 ? width / (values.length - 1) : 0;

  const points = values.map((v, i) => {
    const x = i * stepX;
    const y = height - ((v - min) / range) * height;
    return [x, y] as const;
  });

  const linePath = points
    .map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`)
    .join(' ');

  const areaPath = `${linePath} L${width.toFixed(1)},${height} L0,${height} Z`;

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className={className}
      role="img"
      aria-label={ariaLabel ?? 'sparkline'}
    >
      <path d={areaPath} fill={fill} opacity={0.12} />
      <path
        d={linePath}
        fill="none"
        stroke={stroke}
        strokeWidth={1.5}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
