'use client';

/**
 * Module `crm` — downstream webhook + pipeline vocabulary.
 *
 * The HMAC secret is a password-shaped input with reveal toggle — we
 * don't echo the persisted secret back from the API after save (same
 * pattern as GitHub tokens), so the field shows empty on reload and
 * the installer knows blank = "leave existing".
 */

import { useState } from 'react';

import type { CRMConfig } from '@/types/modules';

import { FieldCard, NumberField, TagInput } from './module-primitives';

export interface ModuleCRMProps {
  value: CRMConfig;
  onChange: (v: CRMConfig) => void;
}

export function ModuleCRM({ value, onChange }: ModuleCRMProps) {
  const [reveal, setReveal] = useState(false);

  return (
    <div className="space-y-4">
      <FieldCard
        title="Webhook outbound"
        hint="SolarLead invia POST firmati con HMAC-SHA256 ad ogni cambio lead."
      >
        <label className="block space-y-1">
          <span className="text-sm text-on-surface">URL webhook</span>
          <input
            type="url"
            value={value.webhook_url ?? ''}
            onChange={(e) =>
              onChange({ ...value, webhook_url: e.target.value || null })
            }
            placeholder="https://crm.example.com/solarlead"
            className="w-full rounded-lg border border-outline-variant/40 bg-surface px-3 py-1.5 text-sm"
          />
        </label>
        <label className="block space-y-1">
          <span className="text-sm text-on-surface">HMAC secret</span>
          <div className="flex gap-2">
            <input
              type={reveal ? 'text' : 'password'}
              value={value.webhook_secret ?? ''}
              onChange={(e) =>
                onChange({
                  ...value,
                  webhook_secret: e.target.value || null,
                })
              }
              placeholder="Lascia vuoto per mantenere il precedente"
              className="flex-1 rounded-lg border border-outline-variant/40 bg-surface px-3 py-1.5 font-mono text-sm"
            />
            <button
              type="button"
              onClick={() => setReveal((r) => !r)}
              className="rounded-lg border border-outline-variant/40 px-3 text-xs text-on-surface-variant hover:bg-surface-container"
            >
              {reveal ? 'Nascondi' : 'Mostra'}
            </button>
          </div>
        </label>
      </FieldCard>

      <FieldCard title="Pipeline">
        <TagInput
          label="Label stati"
          value={value.pipeline_labels}
          onChange={(v) => onChange({ ...value, pipeline_labels: v })}
          placeholder="nuovo, contattato, preventivo"
        />
        <NumberField
          label="SLA primo contatto"
          value={value.sla_hours_first_touch}
          onChange={(v) =>
            onChange({ ...value, sla_hours_first_touch: v ?? 0 })
          }
          suffix="h"
          min={0}
          max={720}
        />
      </FieldCard>
    </div>
  );
}
