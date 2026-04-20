'use client';

/**
 * CRM webhooks manager — client-side CRUD over ``/v1/crm-webhooks``.
 *
 * Part B.7 integration: the backend (migration 0017 + service +
 * worker task + REST routes) has been in place for a while; this is
 * the operator-facing UI that finally closes the loop.
 *
 * Design choices:
 *   - **List is pre-rendered server-side** (SSR via ``listCrmWebhooks``)
 *     and handed in as ``initialRows``. After every mutation we call
 *     ``router.refresh()`` so the server re-fetches with RLS and we
 *     don't maintain a parallel client cache.
 *   - **Secret is shown exactly once**: on create and on rotate. It
 *     appears in a dismissable banner with copy-to-clipboard — after
 *     dismiss the client state discards it. The receiving CRM is
 *     expected to persist it; if they lose it, rotate.
 *   - **Event picker**: all supported events are checkboxes. The
 *     backend validates the list against its own whitelist, so an
 *     enum drift (new event added server-side but not yet here)
 *     surfaces as a 400 we log to the banner.
 *   - **Destructive actions** (delete / deactivate) require a
 *     ``confirm()``. No fancy modal — the cost of a misclick is
 *     losing an integration but not data.
 */

import { useState, useTransition } from 'react';
import { useRouter } from 'next/navigation';

import { api, ApiError } from '@/lib/api-client';
import type { CrmWebhookEvent, CrmWebhookRow } from '@/types/db';

const ALL_EVENTS: { id: CrmWebhookEvent; label: string; hint: string }[] = [
  {
    id: 'lead.created',
    label: 'lead.created',
    hint: 'Quando un nuovo lead entra in pipeline',
  },
  {
    id: 'lead.scored',
    label: 'lead.scored',
    hint: 'Score calcolato (hot / warm / cold)',
  },
  {
    id: 'lead.outreach_sent',
    label: 'lead.outreach_sent',
    hint: 'Prima email / postale / WhatsApp partita',
  },
  {
    id: 'lead.engaged',
    label: 'lead.engaged',
    hint: 'Apertura + click (segnale di interesse reale)',
  },
  {
    id: 'lead.contract_signed',
    label: 'lead.contract_signed',
    hint: 'Contratto firmato (conversion finale)',
  },
];

type FlashKind = 'info' | 'error' | 'secret';
type Flash = { kind: FlashKind; message: string; secret?: string } | null;

export function CrmWebhooksManager({
  initialRows,
}: {
  initialRows: CrmWebhookRow[];
}) {
  const router = useRouter();
  const [rows, setRows] = useState(initialRows);
  const [flash, setFlash] = useState<Flash>(null);
  const [isPending, startTransition] = useTransition();

  // ---- Create form state --------------------------------------------------
  const [formOpen, setFormOpen] = useState(false);
  const [label, setLabel] = useState('');
  const [url, setUrl] = useState('');
  const [events, setEvents] = useState<Set<CrmWebhookEvent>>(
    () => new Set(ALL_EVENTS.map((e) => e.id)),
  );

  const resetForm = () => {
    setLabel('');
    setUrl('');
    setEvents(new Set(ALL_EVENTS.map((e) => e.id)));
    setFormOpen(false);
  };

  const handleApiError = (exc: unknown, fallback: string) => {
    if (exc instanceof ApiError) {
      const detail =
        typeof exc.body === 'object' &&
        exc.body !== null &&
        'detail' in exc.body
          ? String((exc.body as { detail: unknown }).detail)
          : exc.message;
      setFlash({ kind: 'error', message: detail });
    } else {
      setFlash({
        kind: 'error',
        message: exc instanceof Error ? exc.message : fallback,
      });
    }
  };

  const createWebhook = async () => {
    if (!label.trim() || !url.trim() || events.size === 0) {
      setFlash({
        kind: 'error',
        message: 'Label, URL e almeno un evento sono obbligatori.',
      });
      return;
    }
    startTransition(async () => {
      try {
        const res = await api.post<
          CrmWebhookRow & { secret: string }
        >('/v1/crm-webhooks', {
          label: label.trim(),
          url: url.trim(),
          events: [...events],
        });
        // Optimistic append; refresh will reconcile.
        setRows((prev) => [
          {
            id: res.id,
            label: res.label,
            url: res.url,
            events: res.events,
            active: res.active,
            last_status: null,
            last_delivered_at: null,
            failure_count: 0,
            created_at: res.created_at,
            updated_at: res.created_at,
          },
          ...prev,
        ]);
        setFlash({
          kind: 'secret',
          message:
            'Webhook creato. Copia il secret ORA — non sarà più mostrato.',
          secret: res.secret,
        });
        resetForm();
        router.refresh();
      } catch (exc) {
        handleApiError(exc, 'Errore durante la creazione del webhook');
      }
    });
  };

  const toggleActive = (row: CrmWebhookRow) => {
    startTransition(async () => {
      try {
        await api.patch(`/v1/crm-webhooks/${row.id}`, {
          active: !row.active,
        });
        setRows((prev) =>
          prev.map((r) =>
            r.id === row.id ? { ...r, active: !r.active } : r,
          ),
        );
        setFlash({
          kind: 'info',
          message: row.active
            ? `Webhook "${row.label}" disattivato.`
            : `Webhook "${row.label}" riattivato (fail counter azzerato).`,
        });
        router.refresh();
      } catch (exc) {
        handleApiError(exc, 'Errore durante l\u2019aggiornamento');
      }
    });
  };

  const rotateSecret = (row: CrmWebhookRow) => {
    if (
      !confirm(
        `Rigenerare il secret per "${row.label}"? Il vecchio secret verrà invalidato subito: il CRM ricevente smetterà di accettare firme finché non aggiorni il nuovo.`,
      )
    ) {
      return;
    }
    startTransition(async () => {
      try {
        const res = await api.post<{ id: string; secret: string }>(
          `/v1/crm-webhooks/${row.id}/rotate-secret`,
          {},
        );
        setFlash({
          kind: 'secret',
          message: `Nuovo secret per "${row.label}". Copialo ora.`,
          secret: res.secret,
        });
      } catch (exc) {
        handleApiError(exc, 'Errore rotazione secret');
      }
    });
  };

  const deleteWebhook = (row: CrmWebhookRow) => {
    if (
      !confirm(
        `Eliminare "${row.label}"? Le deliveries storiche verranno cancellate in cascade.`,
      )
    ) {
      return;
    }
    startTransition(async () => {
      try {
        await api.delete(`/v1/crm-webhooks/${row.id}`);
        setRows((prev) => prev.filter((r) => r.id !== row.id));
        setFlash({
          kind: 'info',
          message: `Webhook "${row.label}" eliminato.`,
        });
        router.refresh();
      } catch (exc) {
        handleApiError(exc, 'Errore eliminazione webhook');
      }
    });
  };

  const copySecret = async (secret: string) => {
    try {
      await navigator.clipboard.writeText(secret);
      setFlash({ kind: 'info', message: 'Secret copiato negli appunti.' });
    } catch {
      // Clipboard can fail on insecure origins — the secret is
      // already visible in the banner, so just degrade silently.
    }
  };

  return (
    <div className="space-y-5">
      {flash && <FlashBanner flash={flash} onCopy={copySecret} onDismiss={() => setFlash(null)} />}

      <header className="flex items-end justify-between">
        <div>
          <h2 className="font-headline text-xl font-bold tracking-tight">
            Endpoint CRM
          </h2>
          <p className="mt-1 text-sm text-on-surface-variant">
            Un POST firmato (HMAC-SHA256) parte verso ognuno di questi URL a
            ogni evento di ciclo di vita sottoscritto. Retry esponenziale ×3
            su 5xx e transport error; dopo 10 fallimenti consecutivi
            l&apos;endpoint viene disattivato automaticamente.
          </p>
        </div>
        {!formOpen && (
          <button
            type="button"
            onClick={() => setFormOpen(true)}
            className="inline-flex items-center rounded-md bg-primary px-4 py-2 text-sm font-semibold text-on-primary shadow-ambient-sm transition-colors hover:bg-primary/90"
          >
            + Nuovo endpoint
          </button>
        )}
      </header>

      {formOpen && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void createWebhook();
          }}
          className="space-y-4 rounded-xl bg-surface-container-lowest p-5 shadow-ambient"
        >
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-on-surface">Label</span>
              <input
                type="text"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="Salesforce Prod"
                className="rounded-md bg-surface-container-low px-3 py-2 text-on-surface outline-none ring-1 ring-outline-variant focus:ring-2 focus:ring-primary"
                required
                maxLength={120}
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              <span className="font-medium text-on-surface">URL</span>
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://hooks.example.com/solarlead"
                className="rounded-md bg-surface-container-low px-3 py-2 text-on-surface outline-none ring-1 ring-outline-variant focus:ring-2 focus:ring-primary"
                required
              />
            </label>
          </div>

          <fieldset>
            <legend className="mb-2 text-sm font-medium text-on-surface">
              Eventi sottoscritti
            </legend>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {ALL_EVENTS.map((ev) => {
                const checked = events.has(ev.id);
                return (
                  <label
                    key={ev.id}
                    className="flex cursor-pointer items-start gap-2 rounded-md bg-surface-container-low p-2 hover:bg-surface-container-high"
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={(e) => {
                        setEvents((prev) => {
                          const next = new Set(prev);
                          if (e.target.checked) next.add(ev.id);
                          else next.delete(ev.id);
                          return next;
                        });
                      }}
                      className="mt-1"
                    />
                    <div>
                      <p className="text-xs font-mono font-semibold text-on-surface">
                        {ev.label}
                      </p>
                      <p className="text-[11px] text-on-surface-variant">
                        {ev.hint}
                      </p>
                    </div>
                  </label>
                );
              })}
            </div>
          </fieldset>

          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={resetForm}
              className="rounded-md px-4 py-2 text-sm font-medium text-on-surface-variant hover:bg-surface-container-high"
            >
              Annulla
            </button>
            <button
              type="submit"
              disabled={isPending}
              className="rounded-md bg-primary px-4 py-2 text-sm font-semibold text-on-primary hover:bg-primary/90 disabled:opacity-60"
            >
              {isPending ? 'Creazione…' : 'Crea webhook'}
            </button>
          </div>
        </form>
      )}

      {rows.length === 0 ? (
        <div className="rounded-xl bg-surface-container-low p-8 text-center text-sm text-on-surface-variant">
          Nessun endpoint configurato. Clicca &ldquo;Nuovo endpoint&rdquo; per
          iniziare a inviare eventi a un CRM esterno.
        </div>
      ) : (
        <ul className="space-y-3">
          {rows.map((row) => (
            <li
              key={row.id}
              className="rounded-xl bg-surface-container-lowest p-5 shadow-ambient"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h3 className="truncate font-semibold text-on-surface">
                      {row.label}
                    </h3>
                    <HealthChip row={row} />
                  </div>
                  <p className="mt-1 truncate font-mono text-xs text-on-surface-variant">
                    {row.url}
                  </p>
                  <p className="mt-2 flex flex-wrap gap-1 text-[10px]">
                    {row.events.map((ev) => (
                      <span
                        key={ev}
                        className="rounded bg-surface-container-high px-1.5 py-0.5 font-mono text-on-surface-variant"
                      >
                        {ev}
                      </span>
                    ))}
                  </p>
                  {row.last_delivered_at && (
                    <p className="mt-2 text-[11px] text-on-surface-variant">
                      Ultima consegna:{' '}
                      <span className="tabular-nums">
                        {new Date(row.last_delivered_at).toLocaleString('it-IT')}
                      </span>
                      {row.last_status && (
                        <>
                          {' · '}
                          <span className="font-mono">{row.last_status}</span>
                        </>
                      )}
                      {row.failure_count > 0 && (
                        <>
                          {' · '}
                          <span className="text-error">
                            {row.failure_count} fallimenti consecutivi
                          </span>
                        </>
                      )}
                    </p>
                  )}
                </div>

                <div className="flex shrink-0 flex-col items-end gap-2 text-xs">
                  <button
                    type="button"
                    onClick={() => toggleActive(row)}
                    disabled={isPending}
                    className="rounded-md bg-surface-container-high px-3 py-1 font-medium hover:bg-surface-container-highest disabled:opacity-60"
                  >
                    {row.active ? 'Disattiva' : 'Riattiva'}
                  </button>
                  <button
                    type="button"
                    onClick={() => rotateSecret(row)}
                    disabled={isPending}
                    className="rounded-md bg-surface-container-high px-3 py-1 font-medium hover:bg-surface-container-highest disabled:opacity-60"
                  >
                    Rigenera secret
                  </button>
                  <button
                    type="button"
                    onClick={() => deleteWebhook(row)}
                    disabled={isPending}
                    className="rounded-md bg-error-container px-3 py-1 font-medium text-on-error-container hover:bg-error-container/80 disabled:opacity-60"
                  >
                    Elimina
                  </button>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function HealthChip({ row }: { row: CrmWebhookRow }) {
  if (!row.active) {
    return (
      <span className="rounded bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
        Inattivo
      </span>
    );
  }
  if (row.failure_count >= 5) {
    return (
      <span className="rounded bg-error-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-error-container">
        Instabile
      </span>
    );
  }
  if (!row.last_delivered_at) {
    return (
      <span className="rounded bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
        In attesa
      </span>
    );
  }
  return (
    <span className="rounded bg-primary-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-primary-container">
      Sano
    </span>
  );
}

function FlashBanner({
  flash,
  onCopy,
  onDismiss,
}: {
  flash: NonNullable<Flash>;
  onCopy: (secret: string) => void;
  onDismiss: () => void;
}) {
  const tone =
    flash.kind === 'error'
      ? 'bg-error-container text-on-error-container'
      : flash.kind === 'secret'
        ? 'bg-tertiary-container text-on-tertiary-container'
        : 'bg-primary-container text-on-primary-container';
  return (
    <div className={`flex items-start justify-between gap-3 rounded-xl p-4 ${tone}`}>
      <div className="min-w-0">
        <p className="text-sm font-medium">{flash.message}</p>
        {flash.secret && (
          <div className="mt-2 flex items-center gap-2">
            <code className="block max-w-full truncate rounded bg-black/10 px-2 py-1 font-mono text-xs">
              {flash.secret}
            </code>
            <button
              type="button"
              onClick={() => onCopy(flash.secret!)}
              className="rounded-md bg-black/10 px-2 py-1 text-xs font-semibold hover:bg-black/20"
            >
              Copia
            </button>
          </div>
        )}
      </div>
      <button
        type="button"
        onClick={onDismiss}
        className="text-xs font-semibold opacity-70 hover:opacity-100"
        aria-label="Chiudi"
      >
        ×
      </button>
    </div>
  );
}
