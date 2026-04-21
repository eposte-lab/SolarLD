'use client';

/**
 * Module `outreach` — active channels + voice.
 *
 * Each channel has its own capacity/cost profile. Postal and Meta need
 * extra provider setup (Pixart account, Meta OAuth) so they default
 * off — a tenant enabling them should be routed to the relevant
 * provider settings page, but that link lives higher up in the UI.
 */

import type { OutreachConfig } from '@/types/modules';

import { FieldCard, Toggle } from './module-primitives';

export interface ModuleOutreachProps {
  value: OutreachConfig;
  onChange: (v: OutreachConfig) => void;
}

export function ModuleOutreach({ value, onChange }: ModuleOutreachProps) {
  // Defensive: if the DB row is stored with a missing `channels` object
  // (predates a schema addition), don't crash on `value.channels.email`.
  const channels = value.channels ?? {
    email: true,
    postal: false,
    whatsapp: false,
    meta_ads: false,
  };
  return (
    <div className="space-y-4">
      <FieldCard
        title="Canali attivi"
        hint="Ogni canale è indipendente. Postal e Meta richiedono setup provider."
      >
        <Toggle
          label="Email"
          hint="Nurture + reply agent (Resend)."
          value={channels.email}
          onChange={(v) =>
            onChange({ ...value, channels: { ...channels, email: v } })
          }
        />
        <Toggle
          label="Lettera fisica"
          hint="Pixart personalised letter (B2C residenziale)."
          value={channels.postal}
          onChange={(v) =>
            onChange({ ...value, channels: { ...channels, postal: v } })
          }
        />
        <Toggle
          label="WhatsApp"
          hint="Dialog360 outbound (richiede account business)."
          value={channels.whatsapp}
          onChange={(v) =>
            onChange({ ...value, channels: { ...channels, whatsapp: v } })
          }
        />
        <Toggle
          label="Meta Lead Ads"
          hint="Campagne Meta per CAP (richiede OAuth Meta)."
          value={channels.meta_ads}
          onChange={(v) =>
            onChange({ ...value, channels: { ...channels, meta_ads: v } })
          }
        />
      </FieldCard>

      <FieldCard title="Tone & CTA">
        <label className="block space-y-1">
          <span className="text-sm text-on-surface">Tone of voice</span>
          <input
            type="text"
            value={value.tone_of_voice ?? ''}
            onChange={(e) =>
              onChange({ ...value, tone_of_voice: e.target.value })
            }
            maxLength={60}
            className="w-full rounded-lg border border-outline-variant/40 bg-surface px-3 py-1.5 text-sm"
          />
        </label>
        <label className="block space-y-1">
          <span className="text-sm text-on-surface">CTA primaria</span>
          <input
            type="text"
            value={value.cta_primary ?? ''}
            onChange={(e) =>
              onChange({ ...value, cta_primary: e.target.value })
            }
            maxLength={80}
            className="w-full rounded-lg border border-outline-variant/40 bg-surface px-3 py-1.5 text-sm"
          />
        </label>
      </FieldCard>
    </div>
  );
}
