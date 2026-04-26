'use client';

/**
 * QuarantineActions — approve / reject buttons for a quarantine_emails row.
 *
 * Client component so it can:
 *   - Call POST /v1/quarantine/{id}/approve|reject via api-client.ts
 *   - Show optimistic loading state
 *   - Trigger a full page reload on success (router.refresh) so the
 *     server component re-fetches the updated list
 */

import { useRouter } from 'next/navigation';
import { useState } from 'react';

import { api } from '@/lib/api-client';

interface QuarantineActionsProps {
  quarantineId: string;
  reviewStatus: 'pending_review' | 'approved' | 'rejected';
}

export function QuarantineActions({
  quarantineId,
  reviewStatus,
}: QuarantineActionsProps) {
  const router = useRouter();
  const [loading, setLoading] = useState<'approve' | 'reject' | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (reviewStatus !== 'pending_review') {
    return (
      <span
        className={
          reviewStatus === 'approved'
            ? 'rounded-full bg-primary-container px-2.5 py-0.5 text-[11px] font-semibold text-on-primary-container'
            : 'rounded-full bg-secondary-container px-2.5 py-0.5 text-[11px] font-semibold text-on-secondary-container'
        }
      >
        {reviewStatus === 'approved' ? '✓ Approvato' : '✗ Scartato'}
      </span>
    );
  }

  async function handleAction(action: 'approve' | 'reject') {
    setLoading(action);
    setError(null);
    try {
      await api.post(`/v1/quarantine/${quarantineId}/${action}`, {});
      router.refresh();
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Errore sconosciuto';
      setError(msg);
    } finally {
      setLoading(null);
    }
  }

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={() => handleAction('approve')}
        disabled={loading !== null}
        className="rounded-lg bg-primary-container px-3 py-1.5 text-xs font-semibold text-on-primary-container transition hover:opacity-80 disabled:opacity-40"
      >
        {loading === 'approve' ? '…' : 'Approva'}
      </button>
      <button
        onClick={() => handleAction('reject')}
        disabled={loading !== null}
        className="rounded-lg bg-secondary-container px-3 py-1.5 text-xs font-semibold text-on-secondary-container transition hover:opacity-80 disabled:opacity-40"
      >
        {loading === 'reject' ? '…' : 'Scarta'}
      </button>
      {error && (
        <span className="text-xs text-error">{error}</span>
      )}
    </div>
  );
}
