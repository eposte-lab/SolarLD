'use client';

/**
 * CampaignOverrideForm — create a new time-boxed override for a campaign.
 *
 * Keeps it simple: label, type, date range, and a freeform JSON patch editor.
 * The JSON editor uses a <textarea> with validation on submit — no heavy
 * dependency, same pattern as the CRM webhook form.
 */

import { useState, useTransition } from 'react';

import { ApiError } from '@/lib/api-client';
import { createCampaignOverride } from '@/lib/data/campaign-overrides';
import { cn } from '@/lib/utils';
import type { CampaignOverrideRow, CampaignOverrideType } from '@/types/db';

const OVERRIDE_TYPES: { value: CampaignOverrideType; label: string; hint: string }[] = [
  {
    value: 'mail',
    label: 'Email',
    hint: "Usa un template o un oggetto diverso per la durata. Patch su outreach_config.",
  },
  {
    value: 'geo_subset',
    label: 'Geo subset',
    hint: "Limita a un sottoinsieme di CAP/province. Patch su sorgente_config.",
  },
  {
    value: 'ab_test',
    label: 'A/B test',
    hint: "Instrada verso un esperimento specifico per la durata.",
  },
  {
    value: 'all',
    label: 'Generico',
    hint: "Patch applicata a tutti i config del modulo specificato nel JSON.",
  },
];

/** Tomorrow at 00:00 local ISO string for a good default start_at */
function tomorrow(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  d.setHours(0, 0, 0, 0);
  return d.toISOString().slice(0, 16); // YYYY-MM-DDTHH:MM for datetime-local input
}

function threeDaysLater(): string {
  const d = new Date();
  d.setDate(d.getDate() + 4);
  d.setHours(0, 0, 0, 0);
  return d.toISOString().slice(0, 16);
}

interface Props {
  campaignId: string;
  onCreated: (row: CampaignOverrideRow) => void;
}

export function CampaignOverrideForm({ campaignId, onCreated }: Props) {
  const [label, setLabel] = useState('');
  const [overrideType, setOverrideType] = useState<CampaignOverrideType>('all');
  const [startAt, setStartAt] = useState(tomorrow());
  const [endAt, setEndAt] = useState(threeDaysLater());
  const [patchJson, setPatchJson] = useState('{}');
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [apiError, setApiError] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();

  const selectedType = OVERRIDE_TYPES.find((t) => t.value === overrideType)!;

  function handleSubmit() {
    setApiError(null);
    setJsonError(null);

    let patch: Record<string, unknown>;
    try {
      patch = JSON.parse(patchJson);
    } catch {
      setJsonError('JSON non valido. Controlla la sintassi.');
      return;
    }

    startTransition(async () => {
      try {
        const row = await createCampaignOverride(campaignId, {
          label: label.trim() || selectedType.label,
          override_type: overrideType,
          start_at: new Date(startAt).toISOString(),
          end_at: new Date(endAt).toISOString(),
          patch,
        });
        onCreated(row);
      } catch (e) {
        if (e instanceof ApiError) {
          const body = e.body as { detail?: unknown } | undefined;
          setApiError(
            typeof body?.detail === 'string'
              ? body.detail
              : `Errore ${e.status}`,
          );
        } else {
          setApiError(e instanceof Error ? e.message : 'Errore sconosciuto');
        }
      }
    });
  }

  return (
    <div className="space-y-4">
      <h3 className="font-headline text-base font-bold text-on-surface">
        Nuovo override
      </h3>

      {/* Label */}
      <div>
        <label className="mb-1 block text-xs font-semibold text-on-surface-variant">
          Etichetta
        </label>
        <input
          type="text"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          placeholder="es. Test email V2, Solo Napoli centro"
          className="w-full rounded-lg border border-outline-variant/60 bg-surface-container-lowest px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:ring-2 focus:ring-primary/40"
        />
      </div>

      {/* Type */}
      <div>
        <p className="mb-2 text-xs font-semibold text-on-surface-variant">Tipo</p>
        <div className="grid grid-cols-2 gap-2">
          {OVERRIDE_TYPES.map((t) => (
            <button
              key={t.value}
              type="button"
              onClick={() => setOverrideType(t.value)}
              className={cn(
                'rounded-xl border px-3 py-2 text-left text-xs transition-colors',
                overrideType === t.value
                  ? 'border-primary bg-primary-container text-on-primary-container'
                  : 'border-outline-variant/40 bg-surface-container-lowest text-on-surface-variant hover:bg-surface-container-low',
              )}
            >
              <span className="block font-semibold">{t.label}</span>
            </button>
          ))}
        </div>
        <p className="mt-1.5 text-xs text-on-surface-variant">{selectedType.hint}</p>
      </div>

      {/* Date range */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="mb-1 block text-xs font-semibold text-on-surface-variant">
            Inizio
          </label>
          <input
            type="datetime-local"
            value={startAt}
            onChange={(e) => setStartAt(e.target.value)}
            className="w-full rounded-lg border border-outline-variant/60 bg-surface-container-lowest px-3 py-2 text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-semibold text-on-surface-variant">
            Fine
          </label>
          <input
            type="datetime-local"
            value={endAt}
            onChange={(e) => setEndAt(e.target.value)}
            className="w-full rounded-lg border border-outline-variant/60 bg-surface-container-lowest px-3 py-2 text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
        </div>
      </div>

      {/* JSON patch */}
      <div>
        <label className="mb-1 block text-xs font-semibold text-on-surface-variant">
          Patch JSON
          <span className="ml-1 font-normal text-on-surface-variant/60">
            — merge superficiale sul config del tipo scelto
          </span>
        </label>
        <textarea
          value={patchJson}
          onChange={(e) => {
            setPatchJson(e.target.value);
            setJsonError(null);
          }}
          rows={5}
          spellCheck={false}
          className={cn(
            'w-full rounded-lg border bg-surface-container-lowest px-3 py-2 font-mono text-xs text-on-surface focus:outline-none focus:ring-2 focus:ring-primary/40',
            jsonError ? 'border-error' : 'border-outline-variant/60',
          )}
        />
        {jsonError && (
          <p className="mt-1 text-xs text-error">{jsonError}</p>
        )}
        <p className="mt-1 text-xs text-on-surface-variant/60">
          Esempio per tipo "email":{' '}
          <code>{'{"tone_of_voice": "friendly", "cta_primary": "Parla con noi"}'}</code>
        </p>
      </div>

      {apiError && (
        <div
          role="alert"
          className="rounded-lg bg-error-container px-4 py-3 text-sm text-on-error-container"
        >
          {apiError}
        </div>
      )}

      <div className="flex justify-end">
        <button
          type="button"
          onClick={handleSubmit}
          disabled={isPending}
          className={cn(
            'rounded-xl bg-primary px-5 py-2 text-sm font-semibold text-on-primary shadow-ambient-sm transition-opacity',
            isPending && 'opacity-60',
          )}
        >
          {isPending ? 'Creazione…' : 'Crea override'}
        </button>
      </div>
    </div>
  );
}
