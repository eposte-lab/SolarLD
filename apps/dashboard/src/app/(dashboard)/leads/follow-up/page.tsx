/**
 * /leads/follow-up — follow-up management hub.
 *
 * Three sections:
 *   1. Trigger automatico: fires the engagement follow-up cron for this
 *      tenant right now (instead of waiting for 08:15 UTC).
 *   2. Invio massivo AI: select all leads or a subset, generate+send
 *      AI follow-up drafts in one action via POST /v1/followup/bulk-draft.
 *   3. Cronologia: last 50 follow-up sends from followup_emails_sent.
 *
 * Individual AI drafting lives on each lead's detail page (the
 * FollowUpDrafter component). This page is for bulk / operational actions.
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';
import { ArrowUpRight } from 'lucide-react';

import { BentoCard } from '@/components/ui/bento-card';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { createSupabaseServerClient } from '@/lib/supabase/server';
import { cn, relativeTime } from '@/lib/utils';
import { FollowupTrigger } from '@/components/follow-up/followup-trigger';
import { FollowupBulkPanel } from '@/components/follow-up/followup-bulk-panel';

export const dynamic = 'force-dynamic';

export default async function FollowupPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const supabase = await createSupabaseServerClient();

  // Fetch last 50 follow-up sends for this tenant
  type FollowupRow = {
    id: string;
    scenario: string | null;
    score_at_send: number | null;
    sent_at: string | null;
    lead_id: string;
    leads:
      | {
          subjects:
            | {
                business_name: string | null;
                owner_first_name: string | null;
                owner_last_name: string | null;
              }
            | Array<{
                business_name: string | null;
                owner_first_name: string | null;
                owner_last_name: string | null;
              }>
            | null;
        }
      | null;
  };
  const recentFollowupsRaw = await supabase
    .from('followup_emails_sent')
    .select(
      'id, scenario, score_at_send, sent_at, lead_id, ' +
      'leads(subjects(business_name, owner_first_name, owner_last_name))'
    )
    .eq('tenant_id', ctx.tenant.id)
    .order('sent_at', { ascending: false })
    .limit(50);
  const recentFollowups = (recentFollowupsRaw.data as unknown as FollowupRow[] | null) ?? [];

  // Count eligible leads (have outreach, not terminal)
  const { count: eligibleCount } = await supabase
    .from('leads')
    .select('id', { count: 'exact', head: true })
    .eq('tenant_id', ctx.tenant.id)
    .not('outreach_sent_at', 'is', null)
    .not('pipeline_status', 'in', '(closed_won,closed_lost,blacklisted)');

  return (
    <div className="space-y-6">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Gestione follow-up
        </p>
        <h1 className="font-headline text-4xl font-bold tracking-tighter">
          Follow-up
        </h1>
        <p className="mt-2 max-w-xl text-sm text-on-surface-variant">
          Avvia il follow-up automatico in anticipo, invia bozze AI a
          gruppi di lead, o vai sul singolo lead per scrivere un messaggio
          personalizzato.
        </p>
      </header>

      {/* Trigger automatico ------------------------------------------- */}
      <BentoCard span="full">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Follow-up automatico
        </p>
        <h2 className="mt-1 font-headline text-2xl font-bold tracking-tighter">
          Avvia subito
        </h2>
        <p className="mt-1 max-w-xl text-sm text-on-surface-variant">
          Ogni mattina il sistema valuta quali lead meritano un follow-up
          (freddi, tiepidi, caldi, interessati, da riattivare) e invia
          l&apos;email più adatta. Questo pulsante lancia la stessa
          valutazione immediatamente per i tuoi{' '}
          <strong>{(eligibleCount ?? 0).toLocaleString('it-IT')}</strong> lead
          idonei — i tempi di attesa per lead vengono comunque rispettati,
          niente duplicati.
        </p>
        <div className="mt-5">
          <FollowupTrigger eligibleCount={eligibleCount ?? 0} />
        </div>
      </BentoCard>

      {/* Invio massivo AI ----------------------------------------------- */}
      <BentoCard span="full">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Follow-up massivo AI
        </p>
        <h2 className="mt-1 font-headline text-2xl font-bold tracking-tighter">
          Genera e invia in blocco
        </h2>
        <p className="mt-1 max-w-xl text-sm text-on-surface-variant">
          Il sistema genera una bozza personalizzata per ogni lead scelto
          (ROI, engagement, dati preventivo). Puoi rivedere le bozze prima
          di inviare oppure spedirle tutte in una volta.
        </p>
        <div className="mt-5">
          <FollowupBulkPanel tenantId={ctx.tenant.id} />
        </div>
      </BentoCard>

      {/* Cronologia follow-up ------------------------------------------ */}
      <BentoCard span="full">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Cronologia
        </p>
        <h2 className="mt-1 font-headline text-2xl font-bold tracking-tighter">
          Ultimi follow-up inviati
        </h2>

        {recentFollowups.length === 0 ? (
          <p className="mt-4 text-sm text-on-surface-variant">
            Nessun follow-up automatico ancora inviato. Il sistema inizia dopo
            che il primo outreach viene spedito ai tuoi lead.
          </p>
        ) : (
          <div className="mt-4 divide-y divide-outline-variant/20">
            {recentFollowups.map((row) => {
              const subj = Array.isArray(row.leads?.subjects)
                ? row.leads.subjects[0]
                : row.leads?.subjects;
              const name =
                subj?.business_name ||
                [subj?.owner_first_name, subj?.owner_last_name]
                  .filter(Boolean)
                  .join(' ') ||
                'Lead';
              return (
                <div
                  key={row.id}
                  className="flex items-center justify-between gap-4 py-3"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-on-surface">
                      {name}
                    </p>
                    <p className="text-xs text-on-surface-variant">
                      Scenario:{' '}
                      <span className="font-mono">{row.scenario}</span>
                      {' · '}score {row.score_at_send ?? '—'}
                      {' · '}
                      {row.sent_at ? relativeTime(row.sent_at) : '—'}
                    </p>
                  </div>
                  <Link
                    href={`/leads/${row.lead_id}`}
                    className={cn(
                      'shrink-0 rounded-lg px-3 py-1.5 text-xs font-medium',
                      'bg-surface-container text-on-surface-variant',
                      'hover:bg-surface-container-high hover:text-on-surface',
                      'transition-colors',
                    )}
                  >
                    <span className="inline-flex items-center gap-1">
                      Apri lead
                      <ArrowUpRight size={12} strokeWidth={2} aria-hidden />
                    </span>
                  </Link>
                </div>
              );
            })}
          </div>
        )}
      </BentoCard>
    </div>
  );
}
