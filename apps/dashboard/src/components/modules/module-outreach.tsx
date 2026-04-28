'use client';

/**
 * Module `outreach` — active channels + voice.
 *
 * Each channel has its own capacity/cost profile. Postal and Meta need
 * extra provider setup (Pixart account, Meta OAuth) so they default
 * off — a tenant enabling them should be routed to the relevant
 * provider settings page, but that link lives higher up in the UI.
 */

import Link from 'next/link';

import type { OutreachConfig } from '@/types/modules';

import { FieldCard, Toggle } from './module-primitives';

export interface ModuleOutreachProps {
  value: OutreachConfig;
  onChange: (v: OutreachConfig) => void;
}

export function ModuleOutreach({ value, onChange }: ModuleOutreachProps) {
  return (
    <div className="space-y-4">
      <FieldCard
        title="Canali attivi"
        hint="Ogni canale è indipendente. Postal e Meta richiedono setup provider."
      >
        <Toggle
          label="Email"
          hint="Nurture + reply agent (Resend)."
          value={value.channels.email}
          onChange={(v) =>
            onChange({ ...value, channels: { ...value.channels, email: v } })
          }
        />
        <Toggle
          label="Lettera fisica"
          hint="Pixart personalised letter (B2C residenziale)."
          value={value.channels.postal}
          onChange={(v) =>
            onChange({ ...value, channels: { ...value.channels, postal: v } })
          }
        />
        <Toggle
          label="WhatsApp"
          hint="Dialog360 outbound (richiede account business)."
          value={value.channels.whatsapp}
          onChange={(v) =>
            onChange({ ...value, channels: { ...value.channels, whatsapp: v } })
          }
        />
        <Toggle
          label="Meta Lead Ads"
          hint="Campagne Meta per CAP (richiede OAuth Meta)."
          value={value.channels.meta_ads}
          onChange={(v) =>
            onChange({ ...value, channels: { ...value.channels, meta_ads: v } })
          }
        />
      </FieldCard>

      <FieldCard title="Tone & CTA">
        <label className="block space-y-1">
          <span className="text-sm text-on-surface">Tone of voice</span>
          <input
            type="text"
            value={value.tone_of_voice}
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
            value={value.cta_primary}
            onChange={(e) =>
              onChange({ ...value, cta_primary: e.target.value })
            }
            maxLength={80}
            className="w-full rounded-lg border border-outline-variant/40 bg-surface px-3 py-1.5 text-sm"
          />
        </label>
      </FieldCard>

      {/* Sprint 9: quick link to email template & cluster A/B management */}
      <div className="rounded-xl border border-outline-variant/30 bg-surface-container-lowest p-3">
        <p className="text-xs text-on-surface-variant">
          Gestisci il template email e i test A/B per cluster →{' '}
          <Link
            href="/settings/email-template"
            className="font-medium text-primary underline underline-offset-2 hover:text-primary/80"
          >
            Template & A/B test
          </Link>
        </p>
      </div>
    </div>
  );
}
