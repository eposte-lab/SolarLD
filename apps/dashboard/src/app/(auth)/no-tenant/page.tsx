'use client';

/**
 * Shown when the authenticated user has a valid Supabase session but
 * no `tenant_members` row exists. This happens in two scenarios:
 *   1. First-time dev setup — account created but tenant not yet seeded.
 *   2. Account was deleted from `tenant_members` but session is still valid.
 *
 * Provide a clear message + a "sign out" escape hatch.
 */

import { BrandLogo } from '@/components/ui/brand-logo';

export default function NoTenantPage() {
  return (
    <main className="flex min-h-screen items-center justify-center bg-surface px-4 py-12">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/15 text-primary ghost-border-strong">
            <BrandLogo size={32} title="SolarLead" />
          </div>
          <span className="font-headline text-4xl font-extrabold tracking-tighter text-primary">
            SolarLead
          </span>
        </div>

        <div className="space-y-4 rounded-xl bg-surface-container-lowest p-8 shadow-ambient">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-secondary">
              Account non configurato
            </p>
            <h2 className="mt-2 font-headline text-xl font-bold text-on-surface">
              Nessun installatore associato
            </h2>
            <p className="mt-2 text-sm text-on-surface-variant">
              Il tuo account è autenticato ma non è collegato a nessun
              tenant installatore. Contatta l&apos;amministratore oppure
              esegui il seed in Supabase Studio.
            </p>
          </div>

          <div className="rounded-lg bg-surface-container-low p-3 font-mono text-xs text-on-surface-variant">
            <p className="font-semibold text-on-surface">SQL da eseguire:</p>
            <pre className="mt-1 whitespace-pre-wrap leading-relaxed">
{`INSERT INTO tenants (business_name, contact_email)
VALUES ('Mio Installatore', 'tua@email.com')
RETURNING id;

-- poi con l'id ottenuto:
INSERT INTO tenant_members
  (tenant_id, user_id, role)
SELECT '<tenant-id>',
       id,
       'owner'
FROM auth.users
WHERE email = 'tua@email.com';`}
            </pre>
          </div>

          <a
            href="/signout"
            className="block w-full rounded-full bg-surface-container-high px-4 py-3 text-center text-sm font-semibold text-on-surface transition-opacity hover:opacity-80"
          >
            Disconnetti e riprova
          </a>
        </div>
      </div>
    </main>
  );
}
