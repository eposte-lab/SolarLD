'use client';

/**
 * EditorialSparkline — minimal inline chart per chip card.
 *
 * No axis, no grid, no labels. Solo una line bianca o amber (32px height).
 * Usato dentro KpiChipCard hero per visualizzare trend storico breve.
 *
 * Uso:
 *   <EditorialSparkline
 *     data={[10, 12, 8, 14, 22, 20, 18]}
 *     tone="mint"
 *     height={32}
 *   />
 */

import { Line, LineChart, ResponsiveContainer } from 'recharts';

import { cn } from '@/lib/utils';

interface Props {
  data: number[];
  tone?: 'white' | 'mint';
  height?: number;
  className?: string;
}

const COLOR = {
  white: '#ECEFF0',
  mint: '#6FCF97',
} as const;

export function EditorialSparkline({
  data,
  tone = 'white',
  height = 32,
  className,
}: Props) {
  // Recharts data shape: [{ v: 10 }, { v: 12 }, ...]
  const series = data.map((v) => ({ v }));
  return (
    <div className={cn('w-full', className)} style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart
          data={series}
          margin={{ top: 4, right: 4, left: 4, bottom: 4 }}
        >
          <Line
            type="monotone"
            dataKey="v"
            stroke={COLOR[tone]}
            strokeWidth={1.5}
            dot={false}
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
