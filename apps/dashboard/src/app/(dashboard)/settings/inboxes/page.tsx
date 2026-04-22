'use client';

/**
 * /settings/inboxes — Multi-inbox management
 *
 * Lists all configured sending inboxes with live send counts and pause status.
 * Allows creating, editing, pausing/unpausing, and deleting inboxes.
 *
 * Design notes:
 *   - Each inbox is an independent sending identity on the tenant's verified
 *     domain (e.g. giuseppe@acme.it, marco@acme.it, info@acme.it).
 *   - The InboxSelector in the API round-robins sends across all active,
 *     non-capped inboxes. If one inbox hits its daily_cap or gets auto-paused
 *     after a Resend error, the others carry the load uninterrupted.
 *   - 250 emails/day = 5 inboxes × 50/day. Adjust daily_cap per inbox.
 */

import { useEffect, useState, useTransition } from 'react';
import { BentoCard } from '@/components/ui/bento-card';
import { apiClient } from '@/lib/api-client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface InboxRow {
  id: string;
  email: string;
  display_name: string;
  reply_to_email: string | null;
  daily_cap: number;
  total_sent_today: number;
  remaining_today: number;
  is_paused: boolean;
  paused_until: string | null;
  pause_reason: string | null;
  last_sent_at: string | null;
  active: boolean;
  created_at: string;
}

interface QuotaSummary {
  total_daily_cap: number;
  sent_today: number;
  remaining_today: number;
  active_inboxes: number;
  paused_inboxes: number;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function InboxesPage() {
  const [inboxes, setInboxes] = useState<InboxRow[]>([]);
  const [quota, setQuota] = useState<QuotaSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [isPending, startTransition] = useTransition();

  async function refresh() {
    try {
      const [inboxRes, quotaRes] = await Promise.all([
        apiClient.get<{ inboxes: InboxRow[] }>('/v1/inboxes'),
        apiClient.get<QuotaSummary>('/v1/inboxes/quota'),
      ]);
      setInboxes(inboxRes.inboxes);
      setQuota(quotaRes);
    } catch (e) {
      setError('Errore nel caricamento delle inbox');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void refresh(); }, []);

  async function handleUnpause(inboxId: string) {
    startTransition(async () => {
      await apiClient.post(`/v1/inboxes/${inboxId}/unpause`, {});
      await refresh();
    });
  }

  async function handleDelete(inboxId: string, email: string) {
    if (!confirm(`Eliminare definitivamente l'inbox ${email}?`)) return;
    startTransition(async () => {
      await apiClient.delete(`/v1/inboxes/${inboxId}`);
      await refresh();
    });
  }

  return (
    <div className="mx-auto max-w-4xl space-y-8">
      <header className="flex items-end justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Impostazioni · Distribuzione invii
          </p>
          <h1 className="mt-1 font-headline text-4xl font-bold tracking-tighter">
            Inbox mittenti
          </h1>
          <p className="mt-2 max-w-xl text-sm text-on-surface-variant">
            Più indirizzi email sullo stesso dominio verificato. Il sistema
            distribuisce gli invii in round-robin rispettando il cap giornaliero
            per inbox. Se un&apos;inbox riceve un errore dal provider viene messa
            in pausa automatica senza bloccare le altre.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setShowForm(true)}
          className="rounded-full bg-gradient-primary px-5 py-2.5 text-sm font-bold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-90"
        >
          + Aggiungi inbox
        </button>
      </header>

      {/* Quota summary strip */}
      {quota && (
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <QuotaChip label="Cap totale / giorno" value={quota.total_daily_cap.toString()} />
          <QuotaChip label="Inviate oggi" value={quota.sent_today.toString()} />
          <QuotaChip
            label="Rimanenti oggi"
            value={quota.remaining_today.toString()}
            accent={quota.remaining_today === 0 ? 'error' : 'primary'}
          />
          <QuotaChip
            label="In pausa"
            value={quota.paused_inboxes.toString()}
            accent={quota.paused_inboxes > 0 ? 'warn' : 'neutral'}
          />
        </div>
      )}

      {/* Inbox list */}
      {loading ? (
        <p className="text-sm text-on-surface-variant">Caricamento…</p>
      ) : error ? (
        <p className="text-sm text-error">{error}</p>
      ) : inboxes.length === 0 ? (
        <BentoCard span="full">
          <div className="py-8 text-center">
            <p className="font-headline text-xl font-bold text-on-surface">
              Nessuna inbox configurata
            </p>
            <p className="mt-2 text-sm text-on-surface-variant">
              Aggiungi la prima inbox per distribuire gli invii su più mittenti.
              Ogni inbox condivide il dominio verificato del tenant.
            </p>
            <button
              type="button"
              onClick={() => setShowForm(true)}
              className="mt-6 rounded-full border border-primary px-5 py-2 text-sm font-semibold text-primary hover:bg-primary/10"
            >
              Aggiungi la prima inbox
            </button>
          </div>
        </BentoCard>
      ) : (
        <div className="space-y-3">
          {inboxes.map((inbox) => (
            <InboxCard
              key={inbox.id}
              inbox={inbox}
              isPending={isPending}
              onUnpause={() => handleUnpause(inbox.id)}
              onDelete={() => handleDelete(inbox.id, inbox.email)}
            />
          ))}
        </div>
      )}

      {/* Create form modal */}
      {showForm && (
        <CreateInboxModal
          onClose={() => setShowForm(false)}
          onCreated={async () => { setShowForm(false); await refresh(); }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// InboxCard
// ---------------------------------------------------------------------------

function InboxCard({
  inbox,
  isPending,
  onUnpause,
  onDelete,
}: {
  inbox: InboxRow;
  isPending: boolean;
  onUnpause: () => void;
  onDelete: () => void;
}) {
  const capPct = inbox.daily_cap > 0
    ? Math.min(100, Math.round((inbox.total_sent_today / inbox.daily_cap) * 100))
    : 0;

  return (
    <div className="rounded-xl border border-outline-variant/40 bg-surface-container-lowest px-5 py-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          {/* Header row */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-sm font-semibold text-on-surface">
              {inbox.email}
            </span>
            {inbox.display_name && (
              <span className="text-xs text-on-surface-variant">
                · {inbox.display_name}
              </span>
            )}
            {!inbox.active && (
              <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
                Disattivata
              </span>
            )}
            {inbox.is_paused && inbox.active && (
              <span className="rounded-full bg-error-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-error-container">
                In pausa
              </span>
            )}
            {!inbox.is_paused && inbox.active && (
              <span className="rounded-full bg-primary-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-primary-container">
                Attiva
              </span>
            )}
          </div>

          {/* Pause info */}
          {inbox.is_paused && inbox.paused_until && (
            <p className="mt-1 text-xs text-error">
              Pausa fino a {new Date(inbox.paused_until).toLocaleTimeString('it-IT', {
                hour: '2-digit', minute: '2-digit',
              })}
              {inbox.pause_reason ? ` · ${inbox.pause_reason}` : ''}
            </p>
          )}

          {/* Daily cap progress bar */}
          <div className="mt-3 flex items-center gap-3">
            <div className="flex-1 overflow-hidden rounded-full bg-surface-container-high">
              <div
                className={`h-2 rounded-full transition-all ${
                  capPct >= 100 ? 'bg-error' : capPct >= 80 ? 'bg-tertiary' : 'bg-primary/60'
                }`}
                style={{ width: `${capPct}%` }}
              />
            </div>
            <span className="shrink-0 text-xs tabular-nums text-on-surface-variant">
              {inbox.total_sent_today} / {inbox.daily_cap} oggi
            </span>
          </div>
        </div>

        {/* Actions */}
        <div className="flex shrink-0 items-center gap-2">
          {inbox.is_paused && (
            <button
              type="button"
              onClick={onUnpause}
              disabled={isPending}
              className="rounded-lg border border-primary px-3 py-1.5 text-xs font-semibold text-primary hover:bg-primary/10 disabled:opacity-50"
            >
              Sblocca
            </button>
          )}
          <button
            type="button"
            onClick={onDelete}
            disabled={isPending}
            className="rounded-lg border border-error/40 px-3 py-1.5 text-xs font-semibold text-error/80 hover:bg-error/10 disabled:opacity-50"
          >
            Elimina
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// QuotaChip
// ---------------------------------------------------------------------------

function QuotaChip({
  label,
  value,
  accent = 'neutral',
}: {
  label: string;
  value: string;
  accent?: 'primary' | 'error' | 'warn' | 'neutral';
}) {
  const valueClass = {
    primary: 'text-primary',
    error: 'text-error',
    warn: 'text-tertiary',
    neutral: 'text-on-surface',
  }[accent];

  return (
    <div className="rounded-lg bg-surface-container-low px-4 py-3">
      <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
        {label}
      </p>
      <p className={`mt-1 font-headline text-2xl font-bold tabular-nums tracking-tighter ${valueClass}`}>
        {value}
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// CreateInboxModal
// ---------------------------------------------------------------------------

function CreateInboxModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => Promise<void>;
}) {
  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [dailyCap, setDailyCap] = useState(50);
  const [replyTo, setReplyTo] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      await apiClient.post('/v1/inboxes', {
        email: email.trim().toLowerCase(),
        display_name: displayName.trim(),
        daily_cap: dailyCap,
        reply_to_email: replyTo.trim() || null,
      });
      await onCreated();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Errore nella creazione';
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="w-full max-w-md rounded-2xl bg-surface p-6 shadow-2xl">
        <h2 className="font-headline text-xl font-bold tracking-tighter">
          Aggiungi inbox mittente
        </h2>
        <p className="mt-1 text-sm text-on-surface-variant">
          L&apos;indirizzo deve essere sul dominio verificato del tuo tenant.
        </p>

        <form onSubmit={submit} className="mt-5 space-y-4">
          <div>
            <label className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              Indirizzo email *
            </label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="giuseppe@tuodominio.it"
              className="mt-1 w-full rounded-lg bg-surface-container-low px-3 py-2.5 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
            />
          </div>

          <div>
            <label className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              Nome mittente
            </label>
            <input
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Giuseppe Rossi"
              maxLength={120}
              className="mt-1 w-full rounded-lg bg-surface-container-low px-3 py-2.5 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
            />
          </div>

          <div>
            <label className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              Cap giornaliero
            </label>
            <input
              type="number"
              required
              min={1}
              max={2000}
              value={dailyCap}
              onChange={(e) => setDailyCap(Number(e.target.value))}
              className="mt-1 w-full rounded-lg bg-surface-container-low px-3 py-2.5 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
            />
            <p className="mt-1 text-[11px] text-on-surface-variant">
              Max email che questa inbox può inviare al giorno. 50 = default sicuro.
            </p>
          </div>

          <div>
            <label className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
              Reply-to (opzionale)
            </label>
            <input
              type="email"
              value={replyTo}
              onChange={(e) => setReplyTo(e.target.value)}
              placeholder="info@tuodominio.it"
              className="mt-1 w-full rounded-lg bg-surface-container-low px-3 py-2.5 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
            />
          </div>

          {error && (
            <p className="rounded-lg bg-error-container px-3 py-2 text-sm text-on-error-container">
              {error}
            </p>
          )}

          <div className="flex gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="flex-1 rounded-full border border-outline-variant px-4 py-2.5 text-sm font-semibold text-on-surface hover:bg-surface-container-low"
            >
              Annulla
            </button>
            <button
              type="submit"
              disabled={loading}
              className="flex-1 rounded-full bg-gradient-primary px-4 py-2.5 text-sm font-bold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {loading ? 'Creazione…' : 'Crea inbox'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
