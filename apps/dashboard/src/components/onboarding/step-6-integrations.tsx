'use client';

/**
 * Step 6 — Optional provider integrations.
 *
 * Collects API keys for the ancillary providers the wizard couldn't
 * infer from business info:
 *   - NeverBounce (email deliverability pre-flight)
 *   - 360dialog   (WhatsApp Business API — token + phone number)
 *   - Resend inbound webhook secret (pre-generated, user copies into
 *     the Resend dashboard along with the inbound route URL).
 *
 * Every field is optional. Skipping the step leaves the tenant in a
 * perfectly usable state — the missing integrations just stay dark
 * on the dashboard until the installer fills them in under /settings.
 *
 * Values are written to `tenants.settings.*` by the API post-hook in
 * `POST /v1/tenant-config` (see apps/api/src/routes/tenant_config.py).
 * Empty strings are filtered server-side so re-submitting with blanks
 * does NOT overwrite previously stored keys.
 */

import type { WizardForm, WizardIntegrations } from './wizard-types';

export interface Step6Props {
  form: WizardForm;
  onChange: (f: WizardForm) => void;
}

interface FieldDef {
  key: keyof WizardIntegrations;
  label: string;
  hint: string;
  placeholder: string;
  type?: 'text' | 'password';
}

const FIELDS: FieldDef[] = [
  {
    key: 'neverbounce_api_key',
    label: 'NeverBounce API key',
    hint: 'Verifica deliverability email prima di ogni invio — salta i bounce hard.',
    placeholder: 'secret_…',
    type: 'password',
  },
  {
    key: 'dialog360_token',
    label: '360dialog API token',
    hint: 'Token WhatsApp Business. Lo trovi nel pannello 360dialog → API.',
    placeholder: 'xxxx…',
    type: 'password',
  },
  {
    key: 'dialog360_business_number',
    label: 'Numero business WhatsApp',
    hint: 'Formato internazionale senza + (es. 393401234567).',
    placeholder: '39…',
    type: 'text',
  },
  {
    key: 'resend_webhook_secret',
    label: 'Resend inbound webhook secret',
    hint: 'Copia questa stringa nel campo "Secret" della route inbound su Resend.',
    placeholder: 'generate_with_openssl_rand_hex_24',
    type: 'password',
  },
];

export function Step6Integrations({ form, onChange }: Step6Props) {
  function update<K extends keyof WizardIntegrations>(
    key: K,
    value: WizardIntegrations[K],
  ) {
    onChange({
      ...form,
      integrations: { ...form.integrations, [key]: value },
    });
  }

  return (
    <section className="space-y-6">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Passo 6 di 6 · opzionale
        </p>
        <h2 className="mt-1 font-headline text-3xl font-bold tracking-tighter">
          Integrazioni fornitori
        </h2>
        <p className="mt-2 max-w-xl text-sm text-on-surface-variant">
          Le chiavi dei servizi esterni. Puoi lasciarle vuote ora e
          inserirle più tardi da <span className="font-medium">Impostazioni → Integrazioni</span>{' '}
          — l&apos;onboarding si chiude comunque.
        </p>
      </header>

      <div className="space-y-4 rounded-xl bg-surface-container-lowest p-6 shadow-ambient-sm">
        {FIELDS.map((f) => (
          <div key={f.key}>
            <label
              htmlFor={`integration-${f.key}`}
              className="flex items-baseline justify-between"
            >
              <span className="text-sm font-semibold text-on-surface">
                {f.label}
              </span>
              <span className="text-[11px] font-medium uppercase tracking-widest text-on-surface-variant">
                opzionale
              </span>
            </label>
            <input
              id={`integration-${f.key}`}
              type={f.type ?? 'text'}
              autoComplete="off"
              spellCheck={false}
              placeholder={f.placeholder}
              value={form.integrations[f.key]}
              onChange={(e) => update(f.key, e.target.value)}
              className="mt-1.5 block w-full rounded-lg border border-outline-variant/40 bg-surface-container-low px-3 py-2 font-mono text-sm text-on-surface shadow-inner placeholder:text-on-surface-variant/60 focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/30"
            />
            <p className="mt-1.5 text-xs text-on-surface-variant">{f.hint}</p>
          </div>
        ))}
      </div>

      <div className="rounded-xl border border-outline-variant/40 bg-surface-container-lowest p-4 text-xs text-on-surface-variant">
        <p>
          Le chiavi sono salvate criptate in <code>tenants.settings</code> e
          non vengono mai esposte ai client browser. Ruotale dal pannello
          impostazioni quando serve.
        </p>
      </div>
    </section>
  );
}
