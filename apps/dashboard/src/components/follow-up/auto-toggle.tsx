'use client';

/**
 * FollowupAutoToggle — switch the per-tenant `followup_auto_enabled`
 * flag. When OFF, both follow-up cron jobs (cold cadence + engagement
 * scenarios) skip every lead of this tenant. Manual sends from the
 * bulk panel below still work.
 *
 * The companion warning banner appears only when the toggle is ON
 * AND there's manual selection in flight, so the operator is aware
 * that a manual send within the cron's 24h window may collide with
 * an auto step. The cron applies a 24h cooldown via
 * `leads.last_followup_sent_at`, so the practical risk is small.
 */

import { useState } from 'react';

import { api, ApiError } from '@/lib/api-client';
import { cn } from '@/lib/utils';
import { AlertTriangle } from 'lucide-react';

interface Props {
  initialEnabled: boolean;
}

export function FollowupAutoToggle({ initialEnabled }: Props) {
  const [enabled, setEnabled] = useState(initialEnabled);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    const next = !enabled;
    setBusy(true);
    setError(null);
    try {
      await api.post('/v1/tenants/me/followup-auto-toggle', { enabled: next });
      setEnabled(next);
    } catch (err: unknown) {
      const msg =
        err instanceof ApiError
          ? `${err.status} — ${err.message}`
          : err instanceof Error
            ? err.message
            : 'Errore inatteso';
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between rounded-xl bg-surface-container px-5 py-4">
        <div>
          <p className="text-sm font-semibold text-on-surface">
            Follow-up automatico
          </p>
          <p className="text-xs text-on-surface-variant">
            {enabled
              ? 'Cold cadence (4/9/14gg) + scenari engagement attivi.'
              : 'Disattivato — solo invii manuali da questa pagina o dal singolo lead.'}
          </p>
        </div>
        <button
          type="button"
          onClick={toggle}
          disabled={busy}
          className={cn(
            'relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors',
            enabled ? 'bg-primary' : 'bg-surface-container-highest',
            busy && 'opacity-50',
          )}
          aria-pressed={enabled}
          aria-label="Attiva o disattiva il follow-up automatico"
        >
          <span
            className={cn(
              'pointer-events-none inline-block h-5 w-5 transform rounded-full bg-on-primary shadow-ambient-sm transition-transform',
              enabled ? 'translate-x-5' : 'translate-x-0',
            )}
          />
        </button>
      </div>

      {enabled && (
        <div className="flex items-start gap-2 rounded-xl bg-warning-container/40 px-4 py-3 text-xs text-on-surface">
          <AlertTriangle size={14} className="mt-0.5 shrink-0 text-warning" />
          <span>
            Auto follow-up attivo. I cron applicano un cooldown di 24h ai
            lead con invio manuale recente, ma per sicurezza disattivalo se
            stai per fare invii manuali su larga scala.
          </span>
        </div>
      )}

      {error && (
        <p className="text-xs text-error">Errore: {error}</p>
      )}
    </div>
  );
}
