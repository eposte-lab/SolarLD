/**
 * Geocentric Autopilot — single-card UX for /territorio.
 *
 * Flow:
 *   1. On mount, calls /v1/territory/auto-prepare. Idempotent: it kicks
 *      off OSM mapping (L0) and the full L1→L6 funnel only if needed.
 *   2. Polls /v1/territory/leads every 8 seconds while leads are still
 *      being created (cap = QUALIFY_FINAL_TARGET, default 10).
 *   3. Renders one card per lead with two action buttons:
 *        * "Genera GIF" -> POST /v1/leads/{id}/regenerate-rendering
 *        * "Invia email" -> POST /v1/leads/{id}/send-outreach
 *      Both endpoints already enqueue the existing Creative + Outreach
 *      ARQ tasks; we just present them per-lead instead of batched.
 *   4. "Riparti da capo" calls /v1/territory/reset and re-triggers
 *      auto-prepare so the operator can iterate.
 */

'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';

import {
  autoPrepareTerritory,
  getTerritoryLeads,
  regenerateLeadRendering,
  resetTerritoryPipeline,
  sendLeadOutreach,
  type TerritoryLead,
  type TerritoryLeadsResponse,
} from '@/lib/data/territory';

const POLL_INTERVAL_MS = 8_000;

interface AutopilotProps {
  initialData: TerritoryLeadsResponse | null;
}

interface RowState {
  renderBusy: boolean;
  sendBusy: boolean;
  message?: { kind: 'ok' | 'err'; text: string };
}

export function TerritorioAutopilot({ initialData }: AutopilotProps) {
  const [data, setData] = useState<TerritoryLeadsResponse | null>(initialData);
  const [note, setNote] = useState<string>('');
  const [bootstrapping, setBootstrapping] = useState<boolean>(true);
  const [resetBusy, setResetBusy] = useState<boolean>(false);
  const [rowState, setRowState] = useState<Record<string, RowState>>({});
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const leads = useMemo(() => data?.leads ?? [], [data]);
  const targetTotal = data?.target_total ?? 10;
  const leadCount = data?.lead_count ?? 0;
  const capReached = data?.cap_reached ?? false;
  const stillScanning = !capReached && leadCount < targetTotal;

  const refresh = useCallback(async () => {
    try {
      const next = await getTerritoryLeads();
      setData(next);
    } catch (err) {
      console.warn('territory leads poll failed', err);
    }
  }, []);

  // Bootstrap: fire auto-prepare once on mount, then poll.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await autoPrepareTerritory();
        if (cancelled) return;
        setNote(res.note);
      } catch (err) {
        if (cancelled) return;
        const msg = err instanceof Error ? err.message : 'auto-prepare failed';
        setErrorMsg(msg);
      } finally {
        if (!cancelled) setBootstrapping(false);
      }
      await refresh();
    })();
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  useEffect(() => {
    if (!stillScanning) return;
    const id = window.setInterval(() => {
      void refresh();
    }, POLL_INTERVAL_MS);
    return () => window.clearInterval(id);
  }, [stillScanning, refresh]);

  const handleRender = async (leadId: string) => {
    setRowState((prev) => ({
      ...prev,
      [leadId]: { ...(prev[leadId] || { renderBusy: false, sendBusy: false }), renderBusy: true },
    }));
    try {
      await regenerateLeadRendering(leadId);
      setRowState((prev) => ({
        ...prev,
        [leadId]: {
          ...(prev[leadId] || { sendBusy: false }),
          renderBusy: false,
          message: { kind: 'ok', text: 'Generazione GIF in coda…' },
        },
      }));
      await refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'render fallito';
      setRowState((prev) => ({
        ...prev,
        [leadId]: {
          ...(prev[leadId] || { sendBusy: false }),
          renderBusy: false,
          message: { kind: 'err', text: msg },
        },
      }));
    }
  };

  const handleSend = async (leadId: string) => {
    if (!window.confirm('Inviare adesso l’email outreach a questo contatto?')) return;
    setRowState((prev) => ({
      ...prev,
      [leadId]: { ...(prev[leadId] || { renderBusy: false, sendBusy: false }), sendBusy: true },
    }));
    try {
      await sendLeadOutreach(leadId);
      setRowState((prev) => ({
        ...prev,
        [leadId]: {
          ...(prev[leadId] || { renderBusy: false }),
          sendBusy: false,
          message: { kind: 'ok', text: 'Invio email accodato.' },
        },
      }));
      await refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'invio fallito';
      setRowState((prev) => ({
        ...prev,
        [leadId]: {
          ...(prev[leadId] || { renderBusy: false }),
          sendBusy: false,
          message: { kind: 'err', text: msg },
        },
      }));
    }
  };

  const handleReset = async () => {
    if (
      !window.confirm(
        'Ripartire da capo? Verranno eliminati i candidati e i lead generati dalla pipeline geocentrica v3.',
      )
    ) {
      return;
    }
    setResetBusy(true);
    setErrorMsg(null);
    try {
      await resetTerritoryPipeline();
      setRowState({});
      const res = await autoPrepareTerritory();
      setNote(res.note);
      await refresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'reset fallito';
      setErrorMsg(msg);
    } finally {
      setResetBusy(false);
    }
  };

  return (
    <section className="space-y-6">
      <div className="rounded-2xl border border-outline-variant bg-surface-container-low p-6">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-xs uppercase tracking-wider text-on-surface-variant">
              Pipeline geocentrica · autopilota
            </p>
            <h2 className="mt-1 text-xl font-semibold text-on-surface">
              {bootstrapping
                ? 'Inizializzazione…'
                : capReached
                  ? `Pool pronto: ${leadCount}/${targetTotal} lead finali`
                  : leadCount > 0
                    ? `${leadCount}/${targetTotal} lead generati · scansione in corso`
                    : 'Generazione lead in corso…'}
            </h2>
            <p className="mt-2 max-w-2xl text-sm text-on-surface-variant">
              {note ||
                'L0 mappatura + L1→L6 funnel girano in background. Quando i lead sono pronti, premi Genera GIF e poi Invia email per ognuno.'}
            </p>
          </div>
          <button
            type="button"
            onClick={handleReset}
            disabled={resetBusy || bootstrapping}
            className="self-start rounded-md border border-outline px-3 py-1.5 text-xs font-medium text-on-surface-variant hover:bg-surface-container-high disabled:opacity-50"
          >
            {resetBusy ? 'Reset in corso…' : 'Riparti da capo'}
          </button>
        </div>

        {errorMsg ? (
          <div className="mt-3 rounded-md border border-error/40 bg-error/10 p-3 text-sm text-error">
            {errorMsg}
          </div>
        ) : null}

        <div className="mt-5 grid grid-cols-2 gap-2 text-xs sm:grid-cols-3">
          <Chip label="Lead generati" value={leadCount} />
          <Chip label="Target" value={targetTotal} />
          <Chip label="Stato" value={capReached ? 'Completo' : stillScanning ? 'In corso' : '—'} />
        </div>
      </div>

      <div className="rounded-2xl border border-outline-variant bg-surface-container-low p-6">
        <div className="mb-4 flex items-baseline justify-between">
          <h3 className="text-lg font-semibold text-on-surface">
            Lead funnel-v3 · azioni manuali
          </h3>
          <p className="text-xs text-on-surface-variant">
            Render GIF e invio email partono <strong>solo</strong> al click.
          </p>
        </div>

        {leads.length === 0 ? (
          <div className="rounded-md border border-dashed border-outline-variant p-8 text-center text-sm text-on-surface-variant">
            {stillScanning
              ? 'Pipeline in esecuzione (Places → scraping → qualità → Solar API → scoring AI → creazione lead). Aggiornamento ogni 8 secondi.'
              : 'Nessun lead disponibile. Usa Riparti da capo per ripetere il pilota.'}
          </div>
        ) : (
          <ul className="divide-y divide-outline-variant">
            {leads.map((lead) => (
              <LeadRow
                key={lead.id}
                lead={lead}
                state={rowState[lead.id]}
                onRender={() => handleRender(lead.id)}
                onSend={() => handleSend(lead.id)}
              />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function LeadRow({
  lead,
  state,
  onRender,
  onSend,
}: {
  lead: TerritoryLead;
  state: RowState | undefined;
  onRender: () => void;
  onSend: () => void;
}) {
  const hasGif = !!lead.rendering_gif_url || !!lead.rendering_image_url;
  const sent = !!lead.outreach_sent_at;

  return (
    <li className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <p className="truncate font-medium text-on-surface">
            {lead.business_name || 'Azienda senza nome'}
          </p>
          {lead.score !== null ? (
            <span
              className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                (lead.score ?? 0) >= 75
                  ? 'bg-primary/15 text-primary'
                  : 'bg-surface-container-high text-on-surface-variant'
              }`}
            >
              {lead.score} · {lead.score_tier ?? '—'}
            </span>
          ) : null}
        </div>
        <p className="mt-0.5 truncate text-xs text-on-surface-variant">
          {[
            lead.decision_maker_email,
            lead.decision_maker_phone,
            lead.sede_operativa_address,
          ]
            .filter(Boolean)
            .join(' · ') || '—'}
        </p>
        {state?.message ? (
          <p
            className={`mt-1 text-xs ${
              state.message.kind === 'ok' ? 'text-primary' : 'text-error'
            }`}
          >
            {state.message.text}
          </p>
        ) : null}
        {sent ? (
          <p className="mt-1 text-xs text-on-surface-variant">
            Email inviata il {new Date(lead.outreach_sent_at!).toLocaleString('it-IT')}
          </p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <button
          type="button"
          onClick={onRender}
          disabled={state?.renderBusy}
          className="rounded-md border border-outline bg-surface px-3 py-1.5 text-xs font-semibold text-on-surface hover:bg-surface-container-high disabled:opacity-50"
        >
          {state?.renderBusy ? 'GIF…' : hasGif ? 'Rigenera GIF' : 'Genera GIF'}
        </button>
        <button
          type="button"
          onClick={onSend}
          disabled={state?.sendBusy || sent}
          className="rounded-md bg-primary px-3 py-1.5 text-xs font-semibold text-on-primary hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {sent ? 'Inviata' : state?.sendBusy ? 'Invio…' : 'Invia email'}
        </button>
      </div>
    </li>
  );
}

function Chip({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-md border border-outline-variant bg-surface px-3 py-2">
      <p className="text-xs uppercase tracking-wider text-on-surface-variant">
        {label}
      </p>
      <p className="mt-0.5 text-lg font-semibold tabular-nums text-on-surface">
        {typeof value === 'number' ? value.toLocaleString('it-IT') : value}
      </p>
    </div>
  );
}
