'use client';

/**
 * EditorialLineChart — line chart minimalista con label inline.
 *
 * Stile (cfr. reference IMG_0923 / IMG_0925):
 *   - Linee sottili bianche + UNA linea amber per la metrica focused
 *   - No CartesianGrid; solo dashed horizontal a percentile presets
 *   - No tooltip esterno → label inline sulla curva con valore + delta
 *   - Custom dot SVG (ring bianco) sui punti, riempito amber sul focus
 *   - Asse X minimal, asse Y nascosto (label inline lo sostituiscono)
 *
 * Uso:
 *   <EditorialLineChart
 *     data={[{ t: '06:00', open: 12, click: 3 }, ...]}
 *     xKey="t"
 *     series={[
 *       { key: 'open', label: 'Aperture', color: 'white' },
 *       { key: 'click', label: 'Click', color: 'amber', focused: true },
 *     ]}
 *     height={220}
 *     yReferenceLines={[25, 50, 75, 100]}
 *     yReferenceLabels={['25%', '50%', '75%', '100%']}
 *   />
 */

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from 'recharts';

import { cn } from '@/lib/utils';

const COLORS = {
  white: '#ECEFF0',
  whiteDim: '#8A9094',
  amber: '#F4A45C',
  amberDim: '#B86F2C',
  grid: 'rgba(255,255,255,0.08)',
} as const;

export interface ChartSeries {
  key: string;
  label: string;
  /** Color theme — `white` is the default / unfocused; `amber` is the focused metric. */
  color?: 'white' | 'amber' | 'whiteDim';
  /** Render the focus dot (filled amber circle) on the last point. */
  focused?: boolean;
}

export interface EditorialLineChartProps<T extends Record<string, unknown>> {
  data: T[];
  /** Field name used for X axis (typically a date/time/category key). */
  xKey: keyof T & string;
  series: ChartSeries[];
  /** Pixel height (default 220). */
  height?: number;
  /** Horizontal reference lines (Y values), e.g. [25, 50, 75, 100]. */
  yReferenceLines?: number[];
  /** Optional labels paired with `yReferenceLines` (rendered on the right). */
  yReferenceLabels?: string[];
  /** Inline label rendered on top of the LAST data point of focused series. */
  inlineLabel?: { value: string; delta?: string };
  className?: string;
}

export function EditorialLineChart<T extends Record<string, unknown>>({
  data,
  xKey,
  series,
  height = 220,
  yReferenceLines,
  yReferenceLabels,
  inlineLabel,
  className,
}: EditorialLineChartProps<T>) {
  return (
    <div className={cn('relative w-full', className)} style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={data}
          margin={{ top: 24, right: 56, left: 0, bottom: 12 }}
        >
          <CartesianGrid
            stroke={COLORS.grid}
            strokeDasharray="3 6"
            vertical={false}
          />

          <XAxis
            dataKey={xKey}
            stroke={COLORS.whiteDim}
            tick={{ fill: COLORS.whiteDim, fontSize: 10, fontWeight: 600 }}
            tickLine={false}
            axisLine={false}
            tickMargin={8}
          />
          <YAxis hide domain={['dataMin', 'dataMax']} />

          {yReferenceLines?.map((y, i) => (
            <ReferenceLine
              key={`yref-${y}`}
              y={y}
              stroke={COLORS.grid}
              strokeDasharray="3 6"
              label={
                yReferenceLabels?.[i]
                  ? {
                      value: yReferenceLabels[i],
                      position: 'right',
                      fill: COLORS.whiteDim,
                      fontSize: 10,
                      fontWeight: 600,
                    }
                  : undefined
              }
            />
          ))}

          {series.map((s) => {
            const stroke =
              s.color === 'amber'
                ? COLORS.amber
                : s.color === 'whiteDim'
                  ? COLORS.whiteDim
                  : COLORS.white;
            return (
              <Line
                key={s.key}
                type="monotone"
                dataKey={s.key}
                stroke={stroke}
                strokeWidth={s.focused ? 2 : 1.5}
                dot={false}
                activeDot={{
                  r: 5,
                  fill: stroke,
                  stroke: '#0A0B0C',
                  strokeWidth: 2,
                }}
                isAnimationActive
                animationDuration={400}
              />
            );
          })}
        </LineChart>
      </ResponsiveContainer>

      {/* Inline label sopra l'ultimo punto della serie focused (se presente).
       * Non rendiamo questa label dentro recharts perché un floating div
       * tailwind ci dà più controllo tipografico per il "57k -8%" feel. */}
      {inlineLabel && (
        <div className="pointer-events-none absolute right-12 top-1.5 flex items-center gap-2">
          <span className="font-headline text-sm font-bold tabular-nums tracking-tighter text-on-surface">
            {inlineLabel.value}
          </span>
          {inlineLabel.delta && (
            <span className="font-headline text-xs font-semibold tabular-nums tracking-tighter text-primary">
              {inlineLabel.delta}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
