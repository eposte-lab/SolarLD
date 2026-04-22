'use client';

/**
 * Module `crm` — pipeline vocabulary and SLA threshold.
 *
 * Webhook subscriptions (URL, HMAC secret, event filters) are managed
 * in the dedicated Integrations page, not here.  This wizard step only
 * covers the fields that are truly "per-install preferences":
 *   - pipeline_labels  — how the operator names their commercial stages
 *   - sla_hours_first_touch — SLA alert threshold for first contact
 *
 * The webhook_url / webhook_secret fields are preserved inside `value`
 * so they survive save round-trips without being wiped; they are just
 * not exposed as editable inputs in this form.
 */

import Link from 'next/link';

import type { CRMConfig } from '@/types/modules';

import { FieldCard, NumberField, TagInput } from './module-primitives';

export interface ModuleCRMProps {
  value: CRMConfig;
  onChange: (v: CRMConfig) => void;
}

export function ModuleCRM({ value, onChange }: ModuleCRMProps) {
  return (
    <div className="space-y-4">
      {/* Webhook callout — directs to the real integration surface */}
      <div className="flex items-start gap-3 rounded-xl border border-outline-variant/30 bg-surface-container-low px-4 py-3">
        <span className="mt-0.5 text-base leading-none">🔗</span>
        <div className="min-w-0">
          <p className="text-sm font-medium text-on-surface">
            Webhook outbound (HMAC-SHA256)
          </p>
          <p className="mt-0.5 text-xs text-on-surface-variant">
            Registra gli endpoint del tuo CRM nella sezione{' '}
            <Link
              href={'/settings/integrations/webhooks'}
              className="font-medium text-primary underline-offset-2 hover:underline"
            >
              Integrazioni → Webhook
            </Link>
            . Puoi iscrivere più endpoint, filtrare per evento e ruotare il
            secret senza passare da qui.
          </p>
        </div>
      </div>

      <FieldCard
        title="Pipeline"
        hint="5 nomi per le tue fasi commerciali (dalla scoperta alla chiusura) e la soglia SLA entro cui un lead contattato deve ricevere risposta."
      >
        <TagInput
          label="Label stati"
          value={value.pipeline_labels}
          onChange={(v) => onChange({ ...value, pipeline_labels: v })}
          placeholder="nuovo, contattato, preventivo"
        />
        <NumberField
          label="SLA primo contatto (ore)"
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
