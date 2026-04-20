/**
 * PageSkeleton — reusable pulsing skeleton used by loading.tsx files.
 *
 * Props:
 *   rows      number of content rows to show (default 3)
 *   hasTable  render a table skeleton instead of cards
 *   hasStat   show 3 stat chips in the header area
 */

import React from 'react';

import { cn } from '@/lib/utils';

function Bone({
  className,
  style,
}: {
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <div
      className={cn(
        'animate-pulse rounded-lg bg-surface-container-high',
        className,
      )}
      style={style}
    />
  );
}

export function PageSkeleton({
  rows = 3,
  hasTable = false,
  hasStat = false,
}: {
  rows?: number;
  hasTable?: boolean;
  hasStat?: boolean;
}) {
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div className="space-y-2">
          <Bone className="h-2.5 w-24" />
          <Bone className="h-10 w-64" />
          <Bone className="h-3 w-80 opacity-60" />
        </div>
        {hasStat && (
          <div className="flex gap-6 rounded-xl bg-surface-container-lowest px-5 py-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="space-y-1.5">
                <Bone className="h-2 w-14" />
                <Bone className="h-6 w-8" />
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Content area */}
      <div className="rounded-2xl bg-surface-container-lowest p-6 shadow-ambient-sm">
        {hasTable ? (
          <TableSkeleton rows={rows} />
        ) : (
          <CardsSkeleton rows={rows} />
        )}
      </div>
    </div>
  );
}

function CardsSkeleton({ rows }: { rows: number }) {
  return (
    <div className="space-y-4">
      <Bone className="h-3 w-32" />
      <div className="grid gap-4 md:grid-cols-3">
        {Array.from({ length: Math.min(rows, 6) }).map((_, i) => (
          <div key={i} className="space-y-2 rounded-xl bg-surface-container p-4">
            <Bone className="h-3 w-20" />
            <Bone className="h-6 w-16" />
            <Bone className="h-2.5 w-full opacity-50" />
          </div>
        ))}
      </div>
    </div>
  );
}

function TableSkeleton({ rows }: { rows: number }) {
  return (
    <div className="space-y-1">
      {/* Header row */}
      <div className="flex gap-4 px-4 py-2">
        {[40, 20, 16, 12, 12].map((w, i) => (
          <Bone key={i} className={`h-2 flex-[${w}]`} style={{ flex: w }} />
        ))}
      </div>
      {/* Data rows */}
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-4 rounded-lg bg-surface-container px-4 py-3"
          style={{ opacity: 1 - i * 0.12 }}
        >
          <Bone className="h-3 flex-[40]" />
          <Bone className="h-2.5 flex-[20]" />
          <Bone className="h-2.5 flex-[16]" />
          <Bone className="h-5 w-16 flex-[12] rounded-full" />
          <Bone className="h-2.5 flex-[12]" />
        </div>
      ))}
    </div>
  );
}
