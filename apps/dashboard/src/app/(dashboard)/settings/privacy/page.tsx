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

import { BentoCard } from '@/components/ui/bento-card';
import { getAuditLog } from '@/lib/data/audit';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { relativeTime } from '@/lib/utils';
import type { AuditLogRow } from '@/types/db';

export const dynamic = 'force-dynamic';

// ---------------------------------------------------------------------------
// Action labels — human-readable translations of audit action keys
// ---------------------------------------------------------------------------

const ACTION_LABELS: Record<string, string> = {
  'lead.feedback_updated': 'Feedback aggiornato',
  'lead.follow_up_sent': 'Follow-up AI inviato',
  'lead.deleted': 'Lead eliminato (GDPR)',
  'config.updated': 'Configurazione aggiornata',
  'webhook.created': 'Webhook creato',
  'webhook.updated': 'Webhook aggiornato',
  'webhook.deleted': 'Webhook eliminato',
  'webhook.rotated': 'Secret webhook ruotato',
};

const TABLE_LABELS: Record<string, string> = {
  leads: 'Lead',
  campaigns: 'Campagna',
  tenants: 'Tenant',
  crm_webhook_subscriptions: 'Webhook',
};

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
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                  <th className="px-5 py-3">Quando</th>
                  <th className="px-5 py-3">Azione</th>
                  <th className="px-5 py-3">Oggetto</th>
                  <th className="px-5 py-3">Attore</th>
                  <th className="px-5 py-3">Dettagli</th>
                </tr>
              </thead>
              <tbody className="bg-surface-container-lowest">
                {rows.map((row, idx) => (
                  <AuditRow key={String(row.id)} row={row} divider={idx !== 0} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </BentoCard>
    </div>
  );
}

// ---------------------------------------------------------------------------

function AuditRow({
  row,
  divider,
}: {
  row: AuditLogRow;
  divider: boolean;
}) {
  const targetLabel =
    row.target_table && row.target_id
      ? `${TABLE_LABELS[row.target_table] ?? row.target_table} ${row.target_id.slice(0, 8)}…`
      : row.target_table
        ? (TABLE_LABELS[row.target_table] ?? row.target_table)
        : '—';

  const actionLabel = ACTION_LABELS[row.action] ?? row.action;
  const isDestructive = row.action.includes('deleted');

  return (
    <tr
      style={
        divider ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' } : undefined
      }
    >
      <td className="whitespace-nowrap px-5 py-3 text-xs text-on-surface-variant">
        {relativeTime(row.at)}
      </td>
      <td className="px-5 py-3">
        <span
          className={
            isDestructive
              ? 'font-semibold text-error'
              : 'font-semibold text-on-surface'
          }
        >
          {actionLabel}
        </span>
      </td>
      <td className="px-5 py-3 font-mono text-xs text-on-surface-variant">
        {targetLabel}
      </td>
      <td className="px-5 py-3 font-mono text-xs text-on-surface-variant">
        {row.actor_user_id ? row.actor_user_id.slice(0, 8) + '…' : 'system'}
      </td>
      <td className="max-w-xs px-5 py-3 text-xs text-on-surface-variant">
        {row.diff ? (
          <span className="font-mono">
            {Object.entries(row.diff)
              .filter(([, v]) => v !== null && v !== undefined)
              .map(([k, v]) => `${k}: ${String(v)}`)
              .join(' · ')
              .slice(0, 120)}
          </span>
        ) : (
          '—'
        )}
      </td>
    </tr>
  );
}

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
