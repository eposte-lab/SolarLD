/**
 * Lead detail skeleton — mirrors the two-column bento grid layout:
 * left column (lead info, timeline) + right column (campaigns, replies).
 */
export default function Loading() {
  return (
    <div className="space-y-6 animate-pulse">
      {/* Back link + title */}
      <div className="space-y-2">
        <div className="h-2.5 w-20 rounded-lg bg-surface-container-high" />
        <div className="h-10 w-72 rounded-lg bg-surface-container-high" />
        <div className="flex gap-2">
          <div className="h-5 w-16 rounded-full bg-surface-container-high" />
          <div className="h-5 w-20 rounded-full bg-surface-container-high" />
        </div>
      </div>

      {/* Bento grid */}
      <div className="grid gap-4 lg:grid-cols-3">
        {/* Left — 2 cols */}
        <div className="space-y-4 lg:col-span-2">
          <BentoCardSkeleton rows={4} />
          <BentoCardSkeleton rows={3} tall />
        </div>
        {/* Right — 1 col */}
        <div className="space-y-4">
          <BentoCardSkeleton rows={3} />
          <BentoCardSkeleton rows={2} />
        </div>
      </div>
    </div>
  );
}

function BentoCardSkeleton({ rows, tall }: { rows: number; tall?: boolean }) {
  return (
    <div
      className={`rounded-2xl bg-surface-container-lowest p-5 shadow-ambient-sm space-y-3 ${
        tall ? 'min-h-[200px]' : ''
      }`}
    >
      <div className="h-2.5 w-28 rounded-lg bg-surface-container-high" />
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="flex items-center gap-3"
          style={{ opacity: 1 - i * 0.15 }}
        >
          <div className="h-3 w-3 shrink-0 rounded-full bg-surface-container-high" />
          <div className="h-3 flex-1 rounded-lg bg-surface-container-high" />
          <div className="h-2.5 w-16 rounded-lg bg-surface-container-high" />
        </div>
      ))}
    </div>
  );
}
