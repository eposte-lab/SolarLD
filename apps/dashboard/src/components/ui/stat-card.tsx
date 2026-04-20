/**
 * KPI card used on the overview page. Pure presentation.
 */

import { cn } from '@/lib/utils';

export function StatCard({
  label,
  value,
  hint,
  accent,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  accent?: 'primary' | 'hot' | 'warm' | 'success';
}) {
  const accentClass =
    accent === 'hot'
      ? 'text-red-600'
      : accent === 'warm'
      ? 'text-amber-600'
      : accent === 'success'
      ? 'text-emerald-600'
      : 'text-primary';

  return (
    <div className="rounded-lg border border-border bg-card p-5">
      <p className="text-xs uppercase tracking-wide text-muted-foreground">{label}</p>
      <p className={cn('mt-2 text-3xl font-semibold', accentClass)}>{value}</p>
      {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}
