/**
 * Settings → Privacy & Compliance.
 *
 * Part B.11 — shows the immutable audit log and GDPR guidance.
 * The audit log is append-only: operators see every mutation their
 * account has performed (or that automated pipelines performed on
 * their behalf) with full actor + diff attribution.
 *
 * GDPR operations per individual lead (export JSON, hard-delete) live
 * on the lead detail page so the operator can act in context.
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { AuditLogTable } from '@/components/privacy/audit-log-table';
import { BentoCard } from '@/components/ui/bento-card';
import { getAuditLog } from '@/lib/data/audit';
import { getCurrentTenantContext } from '@/lib/data/tenant';

export const dynamic = 'force-dynamic';

// ---------------------------------------------------------------------------

export default async function PrivacyPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const rows = await getAuditLog(100);

  return (
    <div className="space-y-8">
      <header className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            <Link
              href="/settings"
              className="hover:text-on-surface hover:underline"
            >
              Impostazioni
            </Link>
            {' · '}Privacy e GDPR
          </p>
          <h1 className="mt-1 font-headline text-4xl font-bold tracking-tighter text-on-surface md:text-5xl">
            Conformità GDPR
          </h1>
          <p className="mt-2 max-w-2xl text-sm text-on-surface-variant">
            Log di audit immutabile di tutte le mutazioni significative —
            feedback, invii manuali, eliminazioni GDPR, rotazioni secret.
            Per esportare o eliminare i dati di un singolo lead, apri la sua
            pagina e usa la sezione{' '}
            <em className="font-semibold">Zona GDPR</em> in fondo.
          </p>
        </div>
      </header>

      {/* GDPR guidance card */}
      <BentoCard span="full">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Guida operativa GDPR
        </p>
        <h2 className="mt-1 font-headline text-xl font-bold tracking-tighter">
          Come gestire una richiesta del soggetto interessato
        </h2>
        <div className="mt-4 grid gap-4 md:grid-cols-3">
          <GdprGuideCard
            step="1"
            title="Richiesta di accesso (Art. 15)"
            description="Apri la pagina del lead → Zona GDPR → «Esporta dati JSON». Il file contiene tutti i dati personali associati."
          />
          <GdprGuideCard
            step="2"
            title="Diritto all'oblio (Art. 17)"
            description="Apri la pagina del lead → Zona GDPR → «Elimina permanentemente». La cascata SQL rimuove anagrafica, tetto, campagne ed eventi."
          />
          <GdprGuideCard
            step="3"
            title="Prova di conformità"
            description="Ogni eliminazione viene registrata nel log qui sotto con timestamp, operatore e dettagli — conservata anche dopo la cancellazione del lead."
          />
        </div>
      </BentoCard>

      {/* Audit log table */}
      <BentoCard span="full" padding="tight">
        <header className="flex items-center justify-between px-2 pb-5 pt-2">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Log di audit
            </p>
            <h2 className="font-headline text-2xl font-bold tracking-tighter">
              Ultime {rows.length} azioni
            </h2>
          </div>
          <span className="rounded-full bg-surface-container px-3 py-1 text-xs font-semibold text-on-surface-variant">
            Sola lettura · append-only
          </span>
        </header>

        {rows.length === 0 ? (
          <div className="rounded-lg bg-surface-container-low p-10 text-center">
            <p className="text-sm text-on-surface-variant">
              Nessuna azione registrata ancora. Le mutazioni significative
              (feedback lead, follow-up inviati, eliminazioni GDPR) compariranno
              qui non appena avvengono.
            </p>
          </div>
        ) : (
          <AuditLogTable rows={rows} />
        )}
      </BentoCard>
    </div>
  );
}

// ---------------------------------------------------------------------------

function GdprGuideCard({
  step,
  title,
  description,
}: {
  step: string;
  title: string;
  description: string;
}) {
  return (
    <div className="rounded-lg bg-surface-container-low p-4">
      <div className="mb-2 flex h-7 w-7 items-center justify-center rounded-full bg-primary-container text-xs font-bold text-on-primary-container">
        {step}
      </div>
      <p className="font-semibold text-on-surface">{title}</p>
      <p className="mt-1 text-xs text-on-surface-variant">{description}</p>
    </div>
  );
}
