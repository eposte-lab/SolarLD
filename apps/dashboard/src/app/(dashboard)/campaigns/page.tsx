/**
 * Campaigns page — Luminous Curator restyle (Fase B).
 *
 * Layout:
 *   - Editorial header
 *   - 5 KPI chips: Totali / Delivery / Open / Click / Failed
 *   - Full-width bento table of recent campaigns with CampaignStatusChip
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { KpiChipCard } from '@/components/ui/kpi-chip-card';
import {
  getCampaignDeliveryStats,
  listCampaigns,
} from '@/lib/data/campaigns';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { cn, formatNumber, formatPercent, relativeTime } from '@/lib/utils';

export const dynamic = 'force-dynamic';

export default async function CampaignsPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const [stats, campaigns] = await Promise.all([
    getCampaignDeliveryStats(),
    listCampaigns(100),
  ]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <header className="flex flex-col gap-1">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Outreach · {stats.total} campagn{stats.total === 1 ? 'a' : 'e'}
        </p>
        <h1 className="font-headline text-4xl font-bold tracking-tighter">
          Campagne
        </h1>
      </header>

      {/* Funnel KPI strip */}
      <BentoGrid cols={6}>
        <div className="md:col-span-2">
          <KpiChipCard
            label="Totali"
            value={formatNumber(stats.total)}
            accent="neutral"
          />
        </div>
        <KpiChipCard
          label="Consegna"
          value={formatPercent(stats.delivery_rate, 1)}
          hint={`${stats.delivered} / ${stats.total}`}
          accent="primary"
        />
        <KpiChipCard
          label="Open rate"
          value={formatPercent(stats.open_rate, 1)}
          hint={`${stats.opened} / ${stats.delivered}`}
          accent="tertiary"
        />
        <KpiChipCard
          label="Click rate"
          value={formatPercent(stats.click_rate, 1)}
          hint={`${stats.clicked} / ${stats.delivered}`}
          accent="primary"
        />
        <KpiChipCard
          label="Failed"
          value={formatNumber(stats.failed)}
          hint="Bounce / 4xx / complaint"
          accent="secondary"
        />
      </BentoGrid>

      {/* Recent campaigns */}
      <BentoCard span="full" padding="tight">
        <header className="flex items-center justify-between px-2 pb-4 pt-2">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Storico
            </p>
            <h2 className="font-headline text-2xl font-bold tracking-tighter">
              Ultime campagne
            </h2>
          </div>
        </header>

        {campaigns.length === 0 ? (
          <div className="rounded-lg bg-surface-container-low p-10 text-center">
            <p className="text-sm text-on-surface-variant">
              Nessuna campagna ancora. Invia la prima outreach da{' '}
              <Link
                href="/leads?tier=hot"
                className="font-semibold text-primary hover:underline"
              >
                un lead hot
              </Link>
              .
            </p>
          </div>
        ) : (
          <div className="overflow-hidden rounded-lg bg-surface-container-low">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                  <th className="px-5 py-3">Step</th>
                  <th className="px-5 py-3">Canale</th>
                  <th className="px-5 py-3">Subject</th>
                  <th className="px-5 py-3">Stato</th>
                  <th className="px-5 py-3">Inviato</th>
                  <th className="px-5 py-3">Engagement</th>
                  <th />
                </tr>
              </thead>
              <tbody className="bg-surface-container-lowest">
                {campaigns.map((c, idx) => (
                  <tr
                    key={c.id}
                    className="transition-colors hover:bg-surface-container-low"
                    style={
                      idx !== 0
                        ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }
                        : undefined
                    }
                  >
                    <td className="px-5 py-4 font-headline font-bold tabular-nums">
                      #{c.sequence_step}
                    </td>
                    <td className="px-5 py-4 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                      {c.channel}
                    </td>
                    <td className="max-w-xs truncate px-5 py-4 font-medium">
                      {c.email_subject ?? c.template_id ?? (
                        <span className="text-on-surface-variant">—</span>
                      )}
                    </td>
                    <td className="px-5 py-4">
                      <CampaignStatusChip status={c.status} />
                    </td>
                    <td className="px-5 py-4 text-xs text-on-surface-variant">
                      {relativeTime(c.sent_at)}
                    </td>
                    <td className="px-5 py-4 text-xs font-semibold">
                      {c.leads?.outreach_clicked_at ? (
                        <span className="text-primary">Click</span>
                      ) : c.leads?.outreach_opened_at ? (
                        <span className="text-primary">Aperto</span>
                      ) : c.status === 'delivered' ||
                        c.leads?.outreach_delivered_at ? (
                        <span className="text-on-surface-variant">
                          Consegnato
                        </span>
                      ) : (
                        <span className="text-on-surface-variant">—</span>
                      )}
                    </td>
                    <td className="px-5 py-4 text-right">
                      <Link
                        href={`/leads/${c.lead_id}`}
                        className="text-xs font-semibold text-primary hover:underline"
                      >
                        lead →
                      </Link>
                    </td>
                  </tr>
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
// Status chip — local to this page (campaign-status enum differs from lead)
// ---------------------------------------------------------------------------

function CampaignStatusChip({ status }: { status: string }) {
  const styles: Record<string, string> = {
    pending: 'bg-surface-container-high text-on-surface-variant',
    sent: 'bg-surface-container-highest text-on-surface',
    delivered: 'bg-primary-container text-on-primary-container',
    failed: 'bg-secondary-container text-on-secondary-container',
    cancelled: 'bg-surface-container-high text-on-surface-variant opacity-70',
  };
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md px-2.5 py-0.5 text-xs font-medium',
        styles[status] ?? 'bg-surface-container-high text-on-surface-variant',
      )}
    >
      {status}
    </span>
  );
}
