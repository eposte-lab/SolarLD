'use client';

/**
 * CampaignCopyEditor — manual copy override per acquisition campaign.
 *
 * Lets the operator hand-write the 4 dynamic email-template variables
 * (subject, opening line, proposition line, CTA) for a specific
 * campaign. When the override is enabled, OutreachAgent uses these
 * exact strings and bypasses the cluster A/B engine for any lead
 * that belongs to this campaign.
 *
 * Use case ("campagna straordinaria"):
 *   The operator runs the prospector on /scoperta, finds amministratori
 *   di condominio (ATECO 68.32), creates a dedicated campaign, and
 *   wants to push a tecnico-impattante angle different from the
 *   tenant's steady-state copy. Manual override === full control.
 *
 * Disabled state == NULL on the column == cluster A/B is used (default).
 *
 * Persists via PATCH /v1/acquisition-campaigns/:id with body
 * `{ custom_copy_override: { enabled, copy_subject, ... } | null }`.
 */

import { useState, useTransition } from 'react';

import { ApiError } from '@/lib/api-client';
import { patchAcquisitionCampaign } from '@/lib/data/campaign-overrides';
import { cn } from '@/lib/utils';
import type {
  AcquisitionCampaignRow,
  CampaignCustomCopyOverride,
} from '@/types/db';

interface Props {
  campaign: AcquisitionCampaignRow;
}

const FIELDS: Array<{
  key: keyof CampaignCustomCopyOverride;
  label: string;
  hint: string;
  placeholder: string;
  rows: number;
}> = [
  {
    key: 'copy_subject',
    label: 'Oggetto email',
    hint:
      'Sostituisce l\'oggetto generato. Tieni 50-70 caratteri, niente CAPS o emoji per evitare flag spam.',
    placeholder: 'Es. Riduci del 40% i costi energia condominiali — caso reale a Bergamo',
    rows: 2,
  },
  {
    key: 'copy_opening_line',
    label: 'Apertura (prima frase del corpo)',
    hint:
      'Identifica il destinatario. Per amministratori condominio: enfatizza il ruolo + lo scenario tipico (es. assemblee, costi millesimali).',
    placeholder:
      'Es. In qualità di amministratore di condominio sa quanto pesi sul bilancio l\'aumento dei costi delle parti comuni…',
    rows: 3,
  },
  {
    key: 'copy_proposition_line',
    label: 'Proposta (cuore del messaggio)',
    hint:
      'Il messaggio chiave: cosa offri, perché ora, su che dati. Per il taglio "tecnico-impattante" spiega kWp installabili + ammortamento + payback.',
    placeholder:
      'Es. Sul tetto del condominio in Via Roma 12 abbiamo stimato 78 kWp installabili — payback 4.2 anni, IRR 22%…',
    rows: 4,
  },
  {
    key: 'cta_primary_label',
    label: 'Etichetta del bottone CTA',
    hint:
      'Massimo ~30 caratteri. Imperativo + concreto: "Scarica la simulazione", "Fissa una call di 15 minuti".',
    placeholder: 'Es. Scarica la simulazione tecnica',
    rows: 1,
  },
];

export function CampaignCopyEditor({ campaign }: Props) {
  const initial: CampaignCustomCopyOverride = {
    enabled: Boolean(campaign.custom_copy_override?.enabled),
    copy_subject: campaign.custom_copy_override?.copy_subject ?? '',
    copy_opening_line: campaign.custom_copy_override?.copy_opening_line ?? '',
    copy_proposition_line:
      campaign.custom_copy_override?.copy_proposition_line ?? '',
    cta_primary_label: campaign.custom_copy_override?.cta_primary_label ?? '',
  };

  const [state, setState] = useState<CampaignCustomCopyOverride>(initial);
  const [isPending, startTransition] = useTransition();
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  function setField<K extends keyof CampaignCustomCopyOverride>(
    key: K,
    value: CampaignCustomCopyOverride[K],
  ) {
    setState((prev) => ({ ...prev, [key]: value }));
  }

  function validateBeforeSave(): string | null {
    if (!state.enabled) return null; // disabling is always valid
    const missing: string[] = [];
    for (const f of FIELDS) {
      const v = (state[f.key] ?? '').toString().trim();
      if (!v) missing.push(f.label);
    }
    if (missing.length > 0) {
      return `Compila tutti i campi prima di attivare: ${missing.join(' · ')}.`;
    }
    return null;
  }

  function handleSave() {
    setError(null);
    const validationErr = validateBeforeSave();
    if (validationErr) {
      setError(validationErr);
      return;
    }

    // If everything blank AND disabled → send NULL to clear the column.
    const hasAnyContent =
      Boolean(state.copy_subject?.trim()) ||
      Boolean(state.copy_opening_line?.trim()) ||
      Boolean(state.copy_proposition_line?.trim()) ||
      Boolean(state.cta_primary_label?.trim());

    const payload =
      !state.enabled && !hasAnyContent
        ? { custom_copy_override: null }
        : {
            custom_copy_override: {
              enabled: state.enabled,
              copy_subject: state.copy_subject?.trim() || undefined,
              copy_opening_line: state.copy_opening_line?.trim() || undefined,
              copy_proposition_line:
                state.copy_proposition_line?.trim() || undefined,
              cta_primary_label: state.cta_primary_label?.trim() || undefined,
            },
          };

    startTransition(async () => {
      try {
        await patchAcquisitionCampaign(campaign.id, payload);
        setSavedAt(Date.now());
        setTimeout(
          () => setSavedAt((cur) => (cur && Date.now() - cur > 2200 ? null : cur)),
          2500,
        );
      } catch (e) {
        if (e instanceof ApiError) {
          const body = e.body as { detail?: unknown } | undefined;
          setError(
            typeof body?.detail === 'string'
              ? body.detail
              : `Errore ${e.status}: salvataggio fallito.`,
          );
        } else {
          setError(e instanceof Error ? e.message : 'Errore sconosciuto');
        }
      }
    });
  }

  function handleClear() {
    setState({
      enabled: false,
      copy_subject: '',
      copy_opening_line: '',
      copy_proposition_line: '',
      cta_primary_label: '',
    });
  }

  return (
    <div className="space-y-5">
      {/* Intro / why this exists */}
      <div className="rounded-xl border border-outline-variant/40 bg-surface-container-lowest p-4 text-sm text-on-surface-variant">
        <p className="font-semibold text-on-surface">
          Cosa fa questo override
        </p>
        <p className="mt-2">
          Quando attivo, ogni lead di questa campagna riceve esattamente queste
          4 stringhe di copy — l&apos;engine A/B automatico viene{' '}
          <strong>ignorato</strong>. Pensato per <em>campagne straordinarie</em>{' '}
          su segmenti specifici (es. amministratori di condominio,
          farmacie, capannoni logistici) dove vuoi controllo totale del
          messaggio.
        </p>
        <p className="mt-2">
          Quando disattivato (o vuoto), torna a usarsi l&apos;A/B per cluster
          (Claude Haiku genera 2 varianti per il cluster_signature del lead e
          ottimizza sul reply rate).
        </p>
      </div>

      {/* Master toggle */}
      <label
        className={cn(
          'flex cursor-pointer items-center justify-between rounded-xl border px-4 py-3 transition-colors',
          state.enabled
            ? 'border-primary/50 bg-primary/5'
            : 'border-outline-variant/40 bg-surface-container-lowest',
        )}
      >
        <div className="space-y-0.5">
          <p className="text-sm font-semibold text-on-surface">
            Override attivo
          </p>
          <p className="text-xs text-on-surface-variant">
            {state.enabled
              ? 'Il copy qui sotto verrà usato per tutti gli invii di questa campagna.'
              : 'Il copy non è attivo — i lead riceveranno il copy A/B automatico per cluster.'}
          </p>
        </div>
        <input
          type="checkbox"
          checked={state.enabled}
          onChange={(e) => setField('enabled', e.target.checked)}
          className="h-5 w-5 cursor-pointer accent-primary"
        />
      </label>

      {/* Fields */}
      <div className="space-y-4">
        {FIELDS.map((f) => {
          const value = (state[f.key] ?? '') as string;
          return (
            <div key={String(f.key)} className="space-y-1.5">
              <label
                htmlFor={`copy-${String(f.key)}`}
                className="block text-xs font-semibold uppercase tracking-widest text-on-surface-variant"
              >
                {f.label}
              </label>
              <textarea
                id={`copy-${String(f.key)}`}
                value={value}
                onChange={(e) => setField(f.key, e.target.value as never)}
                placeholder={f.placeholder}
                rows={f.rows}
                disabled={!state.enabled}
                className={cn(
                  'w-full rounded-xl border bg-surface-container-lowest px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-variant/50',
                  'focus:border-primary focus:outline-none focus:ring-1 focus:ring-primary',
                  !state.enabled && 'cursor-not-allowed opacity-60',
                  'border-outline-variant/40',
                )}
              />
              <p className="text-xs text-on-surface-variant">{f.hint}</p>
            </div>
          );
        })}
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-xl bg-error-container px-4 py-3 text-sm text-on-error-container"
        >
          {error}
        </div>
      )}

      {savedAt && (
        <p className="text-sm font-semibold text-primary">
          ✓ Copy personalizzato salvato.
        </p>
      )}

      <div className="flex flex-wrap items-center justify-end gap-2">
        <button
          type="button"
          onClick={handleClear}
          disabled={isPending}
          className="rounded-xl ghost-border bg-surface-container-lowest px-4 py-2 text-sm font-semibold text-on-surface-variant hover:bg-white/5 hover:text-on-surface"
        >
          Svuota tutto
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={isPending}
          className={cn(
            'rounded-xl bg-primary px-5 py-2 text-sm font-semibold text-on-primary shadow-ambient-sm transition-opacity',
            isPending && 'opacity-60',
          )}
        >
          {isPending
            ? 'Salvataggio…'
            : state.enabled
              ? 'Salva e attiva override'
              : 'Salva (override disattivato)'}
        </button>
      </div>
    </div>
  );
}
