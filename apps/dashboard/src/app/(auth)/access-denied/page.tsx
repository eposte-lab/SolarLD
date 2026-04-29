'use client';

/**
 * Shown when the device-authorization gate (migration 0074) blocks
 * a login because the tenant has reached its `demo_device_max_total`
 * cap and no slot is available for this device.
 *
 * The user is still authenticated by Supabase at this point — the
 * block is *application-level*. We give them a clear message + a
 * sign-out escape hatch so they don't get stuck.
 */

import { BrandLogo } from '@/components/ui/brand-logo';

export default function AccessDeniedPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-surface px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl bg-error/15 text-error ghost-border-strong">
            <BrandLogo size={32} title="SolarLead" />
          </div>
          <span className="font-headline text-4xl font-extrabold tracking-tighter text-primary">
            SolarLead
          </span>
        </div>

        <div className="space-y-4 rounded-xl bg-surface-container-lowest p-8 shadow-ambient">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-error">
              Accesso bloccato
            </p>
            <h2 className="mt-2 font-headline text-xl font-bold text-on-surface">
              Numero massimo dispositivi raggiunto
            </h2>
            <p className="mt-2 text-sm text-on-surface-variant">
              L&apos;account demo ha già il numero massimo di dispositivi
              autorizzati. Per accedere da un nuovo dispositivo, contatta
              l&apos;amministratore: dovrà revocare un dispositivo esistente
              dal pannello{' '}
              <span className="font-mono">/settings/devices</span>.
            </p>
          </div>

          <div className="rounded-lg bg-surface-container-low p-3 text-xs text-on-surface-variant">
            <p className="font-semibold text-on-surface">Cosa puoi fare:</p>
            <ul className="mt-2 list-disc space-y-1 pl-4">
              <li>Riprova dal dispositivo già autorizzato.</li>
              <li>
                Chiedi all&apos;amministratore di liberare uno slot revocando
                un dispositivo che non usi più.
              </li>
              <li>
                Se sei l&apos;amministratore, accedi dal tuo dispositivo
                principale e gestisci gli slot.
              </li>
            </ul>
          </div>

          <a
            href="/signout"
            className="block w-full rounded-full bg-surface-container-high px-4 py-3 text-center text-sm font-semibold text-on-surface transition-opacity hover:opacity-80"
          >
            Disconnetti
          </a>
        </div>
      </div>
    </main>
  );
}
