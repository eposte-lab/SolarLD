'use client';

/**
 * CampaignOverrideList — renders the list of overrides for a campaign
 * and allows deleting them. The "Aggiungi override" CTA opens the
 * CampaignOverrideForm below via a controlled `showForm` state.
 */

import { useState, useTransition } from 'react';

import { deleteCampaignOverride } from '@/lib/data/campaign-overrides';
import { cn, relativeTime } from '@/lib/utils';
import type { CampaignOverrideRow } from '@/types/db';

import { CampaignOverrideForm } from './CampaignOverrideForm';

interface Props {
  campaignId: string;
  initialOverrides: CampaignOverrideRow[];
}

const OVERRIDE_TYPE_LABELS: Record<string, string> = {
  mail: 'Email',
  geo_subset: 'Geo subset',
  ab_test: 'A/B test',
  all: 'Generico',
};

function isActive(override: CampaignOverrideRow): boolean {
  const now = Date.now();
  return (
    new Date(override.start_at).getTime() <= now &&
    new Date(override.end_at).getTime() >= now
  );
}

export function CampaignOverrideList({ campaignId, initialOverrides }: Props) {
  const [overrides, setOverrides] = useState(initialOverrides);
  const [showForm, setShowForm] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [, startTransition] = useTransition();

  function handleCreated(row: CampaignOverrideRow) {
    setOverrides((prev) => [row, ...prev]);
    setShowForm(false);
  }

  function handleDelete(id: string) {
    setDeletingId(id);
    startTransition(async () => {
      try {
        await deleteCampaignOverride(campaignId, id);
        setOverrides((prev) => prev.filter((o) => o.id !== id));
      } finally {
        setDeletingId(null);
      }
    });
  }

  const active = overrides.filter(isActive);
  const upcoming = overrides.filter(
    (o) => new Date(o.start_at).getTime() > Date.now(),
  );
  const past = overrides.filter(
    (o) => new Date(o.end_at).getTime() < Date.now(),
  );

  return (
    <div className="space-y-4">
      {/* Header + CTA */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-on-surface-variant">
          {overrides.length === 0
            ? 'Nessun override configurato.'
            : `${active.length} attiv${active.length === 1 ? 'o' : 'i'}, ${upcoming.length} in arrivo, ${past.length} scadut${past.length === 1 ? 'o' : 'i'}.`}
        </p>
        <button
          type="button"
          onClick={() => setShowForm((s) => !s)}
          className="rounded-xl bg-primary px-4 py-1.5 text-xs font-semibold text-on-primary shadow-ambient-sm"
        >
          {showForm ? 'Annulla' : '+ Aggiungi override'}
        </button>
      </div>

      {/* Inline form */}
      {showForm && (
        <div className="rounded-xl border border-outline-variant/40 bg-surface-container-lowest p-4">
          <CampaignOverrideForm campaignId={campaignId} onCreated={handleCreated} />
        </div>
      )}

      {/* List */}
      {overrides.length > 0 && (
        <div className="space-y-2">
          {overrides.map((o) => (
            <OverrideRow
              key={o.id}
              override={o}
              deleting={deletingId === o.id}
              onDelete={() => handleDelete(o.id)}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function OverrideRow({
  override,
  deleting,
  onDelete,
}: {
  override: CampaignOverrideRow;
  deleting: boolean;
  onDelete: () => void;
}) {
  const active = isActive(override);
  const past = new Date(override.end_at).getTime() < Date.now();

  return (
    <div
      className={cn(
        'flex items-start justify-between gap-3 rounded-xl border px-4 py-3',
        active
          ? 'border-primary/30 bg-primary-container/20'
          : past
            ? 'border-outline-variant/20 bg-surface-container opacity-60'
            : 'border-outline-variant/40 bg-surface-container-lowest',
      )}
    >
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex flex-wrap items-center gap-2">
          {active && (
            <span className="rounded-full bg-primary-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-primary-container">
              Attivo
            </span>
          )}
          {!active && !past && (
            <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
              In arrivo
            </span>
          )}
          <span className="text-sm font-semibold text-on-surface">
            {override.label || OVERRIDE_TYPE_LABELS[override.override_type] || override.override_type}
          </span>
          <span className="text-xs text-on-surface-variant">
            {OVERRIDE_TYPE_LABELS[override.override_type]}
          </span>
        </div>
        <p className="text-xs text-on-surface-variant">
          {new Date(override.start_at).toLocaleDateString('it-IT')}
          {' → '}
          {new Date(override.end_at).toLocaleDateString('it-IT')}
          {' · '}
          creato {relativeTime(override.created_at)}
        </p>
        {Object.keys(override.patch).length > 0 && (
          <p className="mt-1 text-xs text-on-surface-variant/70">
            Patch: {Object.keys(override.patch).join(', ')}
          </p>
        )}
      </div>

      <button
        type="button"
        onClick={onDelete}
        disabled={deleting}
        className="shrink-0 rounded-lg border border-outline-variant/40 px-2.5 py-1 text-xs text-on-surface-variant hover:bg-error-container hover:text-on-error-container disabled:opacity-40"
      >
        {deleting ? '…' : 'Elimina'}
      </button>
    </div>
  );
}
