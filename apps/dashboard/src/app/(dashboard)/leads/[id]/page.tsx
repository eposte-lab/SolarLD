/**
 * Lead detail — Luminous Curator restyle (Fase B).
 *
 * Layout:
 *   Row 1 — Header (breadcrumb, name, chips) + sticky Send CTA
 *   Row 2 — Hero rendering (full-width bento, overlaid GlassPanel with name + address)
 *   Row 3 — ROI bento grid (4 KpiChipCards)
 *   Row 4 — Subject + Roof (2 bento cards, ghost-border data rows)
 *   Row 5 — Outreach sequence (bento)
 *   Row 6 — Timeline (bento)
 */

import {
  AlertTriangle,
  ArrowLeft,
  ArrowUpRight,
  Check,
  ExternalLink,
} from 'lucide-react';
import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';

import { FollowUpDrafter } from '@/components/follow-up-drafter';
import { LeadConversationsCard } from '@/components/lead-conversations-card';
import { LeadRepliesCard } from '@/components/lead-replies-card';
import { LeadTimelineLive } from '@/components/lead-timeline-live';
import { LeadGdprActionsWrapper } from './LeadGdprActionsWrapper';
import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { GlassPanel } from '@/components/ui/glass-panel';
import { KpiChipCard } from '@/components/ui/kpi-chip-card';
import { EngagementScoreChip } from '@/components/ui/engagement-score-chip';
import { StatusChip, TierChip } from '@/components/ui/status-chip';
import { TierLock } from '@/components/ui/tier-lock';
import { listCampaignsForLead, listEventsForLead } from '@/lib/data/campaigns';
import { getLeadById } from '@/lib/data/leads';
import { getConversationsForLead } from '@/lib/data/conversations';
import { getLeadReplies } from '@/lib/data/replies';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { canTenantUse } from '@/lib/data/tier';
import {
  formatDate,
  formatEurPlain,
  formatNumber,
  relativeTime,
} from '@/lib/utils';

import { SendOutreachButton } from './SendOutreachButton';
import { SolarApiInspector } from './SolarApiInspector';

export const dynamic = 'force-dynamic';

type PageProps = { params: Promise<{ id: string }> };

export default async function LeadDetailPage({ params }: PageProps) {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const { id } = await params;
  const [lead, campaigns, events, replies, conversations] = await Promise.all([
    getLeadById(id),
    listCampaignsForLead(id),
    listEventsForLead(id),
    getLeadReplies(id),
    getConversationsForLead(id),
  ]);
  if (!lead) notFound();

  const name =
    lead.subjects?.business_name ||
    [lead.subjects?.owner_first_name, lead.subjects?.owner_last_name]
      .filter(Boolean)
      .join(' ') ||
    '—';
  const address = [lead.roofs?.address, lead.roofs?.cap, lead.roofs?.comune]
    .filter(Boolean)
    .join(', ');
  const isBlacklisted = lead.pipeline_status === 'blacklisted';
  const portalUrl =
    process.env.NEXT_PUBLIC_LEAD_PORTAL_URL || 'http://localhost:3001';
  const publicLeadLink = `${portalUrl}/lead/${lead.public_slug}`;
  const alreadySent = lead.outreach_sent_at != null;

  return (
    <div className="space-y-8">
      {/* Header -------------------------------------------------------- */}
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="space-y-3">
          <Link
            href="/leads"
            className="inline-flex items-center gap-1 text-xs font-medium text-on-surface-variant transition-colors hover:text-primary"
          >
            <ArrowLeft size={12} strokeWidth={2.25} aria-hidden />
            Tutti i lead
          </Link>
          <h1 className="font-headline text-4xl font-bold tracking-tighter">
            {name}
          </h1>
          <div className="flex flex-wrap items-center gap-2">
            <TierChip tier={lead.score_tier} />
            <EngagementScoreChip
              score={lead.engagement_score}
              updatedAt={lead.engagement_score_updated_at}
            />
            <StatusChip status={lead.pipeline_status} />
            <span className="text-xs text-on-surface-variant">
              Score{' '}
              <span className="font-headline font-bold text-on-surface">
                {lead.score}
              </span>
            </span>
            <span className="text-xs text-on-surface-variant">·</span>
            <span className="text-xs text-on-surface-variant">
              Creato {formatDate(lead.created_at)}
            </span>
            <span className="text-xs text-on-surface-variant">·</span>
            <a
              href={publicLeadLink}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline"
            >
              Pagina pubblica
              <ExternalLink size={11} strokeWidth={2.25} aria-hidden />
            </a>
          </div>
        </div>

        {!isBlacklisted && (
          <SendOutreachButton leadId={lead.id} alreadySent={alreadySent} />
        )}
      </header>

      {/* Hero rendering ----------------------------------------------- */}
      {lead.rendering_image_url && (
        <div className="relative overflow-hidden rounded-xl shadow-ambient">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={lead.rendering_image_url}
            alt={`Rendering impianto ${name}`}
            className="aspect-[21/9] w-full object-cover"
          />
          {address && (
            <GlassPanel className="absolute bottom-5 left-5 max-w-sm px-5 py-3">
              <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                Indirizzo
              </p>
              <p className="font-headline text-sm font-bold text-on-surface">
                {address}
              </p>
            </GlassPanel>
          )}
        </div>
      )}

      {/* Video / GIF rendering ---------------------------------------- */}
      {(lead.rendering_video_url || lead.rendering_gif_url) && (
        <BentoCard title="Video rendering" padding="tight" span="full">
          <div className="flex flex-col gap-4 p-4 sm:flex-row sm:items-start">
            {/* Video player — uses GIF as poster frame */}
            {lead.rendering_video_url ? (
              // eslint-disable-next-line jsx-a11y/media-has-caption
              <video
                src={lead.rendering_video_url}
                poster={lead.rendering_gif_url ?? undefined}
                controls
                muted
                loop
                playsInline
                className="w-full rounded-lg sm:max-w-md"
              />
            ) : lead.rendering_gif_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={lead.rendering_gif_url}
                alt="GIF rendering fotovoltaico"
                className="w-full rounded-lg sm:max-w-md"
              />
            ) : null}
            <div className="flex flex-col gap-3">
              <p className="text-sm text-on-surface-variant">
                Rendering fotovoltaico generato per questo lead. Il video è
                incluso nelle email inviate come hero cliccabile.
              </p>
              {lead.portal_video_slug && (
                <a
                  href={`${process.env.NEXT_PUBLIC_LEAD_PORTAL_URL ?? ''}/lead/${lead.portal_video_slug}/video`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary transition-opacity hover:opacity-90"
                >
                  ▶ Apri landing video
                </a>
              )}
            </div>
          </div>
        </BentoCard>
      )}

      {/* ROI chips ----------------------------------------------------- */}
      <BentoGrid cols={4}>
        <KpiChipCard
          label="Potenza stimata"
          value={
            lead.roi_data?.estimated_kwp != null
              ? `${formatNumber(lead.roi_data.estimated_kwp)} kWp`
              : '—'
          }
          accent="primary"
        />
        <KpiChipCard
          label="Risparmio annuo"
          value={formatEurPlain(lead.roi_data?.annual_savings_eur ?? null)}
          accent="primary"
        />
        <KpiChipCard
          label="Rientro investimento"
          value={
            lead.roi_data?.payback_years != null
              ? `${formatNumber(lead.roi_data.payback_years)} anni`
              : '—'
          }
          accent="tertiary"
        />
        <KpiChipCard
          label="CO₂ evitata"
          value={
            lead.roi_data?.co2_saved_kg != null
              ? `${formatNumber(lead.roi_data.co2_saved_kg)} kg`
              : '—'
          }
          accent="neutral"
        />
      </BentoGrid>

      {/* Solar API inspector ----------------------------------------- */}
      {/* Surfaces panel count, dominant azimuth, per-segment data and the
          raw Solar API payload — operators sanity-check the quote here
          before the email goes out. Includes the "Rigenera rendering"
          control to re-run the AI paint pipeline if the data looks off. */}
      <SolarApiInspector lead={lead} />

      {/* Subject + Roof ----------------------------------------------- */}
      <BentoGrid cols={2}>
        <DataCard title="Anagrafica">
          <DataRow
            label="Tipo"
            value={lead.subjects?.type?.toUpperCase() ?? '—'}
          />
          <DataRow
            label="Ragione sociale"
            value={lead.subjects?.business_name ?? '—'}
          />
          <DataRow
            label="Referente"
            value={
              [
                lead.subjects?.owner_first_name,
                lead.subjects?.owner_last_name,
              ]
                .filter(Boolean)
                .join(' ') || '—'
            }
          />
          <DataRow
            label="Email"
            value={
              lead.subjects?.decision_maker_email ? (
                <span className="inline-flex items-center justify-end gap-1.5">
                  {lead.subjects.decision_maker_email}
                  {lead.subjects.decision_maker_email_verified ? (
                    <Check
                      size={12}
                      strokeWidth={2.5}
                      className="text-primary"
                      aria-label="Verificata"
                    />
                  ) : (
                    <AlertTriangle
                      size={12}
                      strokeWidth={2.25}
                      className="text-warning"
                      aria-label="Non verificata"
                    />
                  )}
                </span>
              ) : (
                '—'
              )
            }
          />
        </DataCard>

        <DataCard title="Tetto / Geo">
          <DataRow label="Indirizzo" value={address || '—'} />
          <DataRow label="Provincia" value={lead.roofs?.provincia ?? '—'} />
          <DataRow
            label="Superficie tetto"
            value={
              lead.roofs?.area_sqm
                ? `${formatNumber(lead.roofs.area_sqm)} m²`
                : '—'
            }
          />
          <DataRow
            label="Producibilità stimata"
            value={
              lead.roofs?.estimated_yearly_kwh
                ? `${formatNumber(lead.roofs.estimated_yearly_kwh)} kWh/anno`
                : '—'
            }
          />
        </DataCard>
      </BentoGrid>

      {/* Outreach sequence -------------------------------------------- */}
      <BentoCard span="full">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Outreach
            </p>
            <h2 className="font-headline text-2xl font-bold tracking-tighter">
              Sequenza campagne
            </h2>
          </div>
        </div>
        {campaigns.length === 0 ? (
          <div className="rounded-lg bg-surface-container-low p-6 text-sm text-on-surface-variant">
            Nessuna campagna ancora. Premi{' '}
            <em className="font-semibold text-primary">Invia outreach</em> per
            attivare il flow.
          </div>
        ) : (
          <ul className="space-y-2">
            {campaigns.map((c) => (
              <li
                key={c.id}
                className="flex items-center justify-between rounded-lg bg-surface-container-low px-5 py-3 text-sm"
              >
                <div className="space-y-0.5">
                  <p className="font-semibold">
                    Step {c.sequence_step} ·{' '}
                    <span className="text-[10px] uppercase tracking-widest text-on-surface-variant">
                      {c.channel}
                    </span>
                  </p>
                  <p className="text-xs text-on-surface-variant">
                    {c.email_subject ?? c.template_id ?? '—'}
                  </p>
                </div>
                <div className="flex items-center gap-3 text-xs text-on-surface-variant">
                  <span>Inviato {relativeTime(c.sent_at)}</span>
                  {/*
                    Engagement (open / click) is tracked at the LEAD
                    level, not per campaign step — the Resend webhook
                    updates `leads.outreach_*_at`. We surface the
                    signal on the most recent sent step so the
                    sequence UI stays informative without over-
                    claiming which specific step was opened.
                   */}
                  {isLatestSent(c, campaigns) && lead.outreach_opened_at && (
                    <span className="font-semibold text-primary">Aperto</span>
                  )}
                  {isLatestSent(c, campaigns) && lead.outreach_clicked_at && (
                    <span className="font-semibold text-primary">Click</span>
                  )}
                  {c.status === 'failed' && (
                    <span className="font-semibold text-secondary">
                      Failed · {c.failure_reason ?? '?'}
                    </span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </BentoCard>

      {/* Follow-up drafter (Pro+) ------------------------------------- */}
      {!isBlacklisted && (
        <BentoCard span="full">
          <div className="mb-4 flex items-start justify-between gap-3">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
                Follow-up assistito
              </p>
              <h2 className="font-headline text-2xl font-bold tracking-tighter">
                Scrivi con AI
              </h2>
              <p className="mt-1 max-w-xl text-sm text-on-surface-variant">
                Claude analizza ROI, engagement e cronologia campagne di questo
                lead e scrive una bozza personalizzata. Modifica e invia.
              </p>
            </div>
          </div>
          <TierLock
            feature="advanced_analytics"
            tenant={ctx.tenant}
            featureLabel="Follow-up drafter AI"
            inline
          >
            <FollowUpDrafter leadId={lead.id} />
          </TierLock>
        </BentoCard>
      )}

      {/* Timeline ----------------------------------------------------- */}
      <BentoCard span="full">
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Cronologia
            </p>
            <h2 className="font-headline text-2xl font-bold tracking-tighter">
              Timeline eventi
            </h2>
          </div>
          {canTenantUse(ctx.tenant, 'realtime_timeline') && (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-primary-container px-2.5 py-1 text-[10px] font-semibold uppercase tracking-widest text-on-primary-container">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75"></span>
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary"></span>
              </span>
              Live
            </span>
          )}
        </div>
        {canTenantUse(ctx.tenant, 'realtime_timeline') ? (
          <LeadTimelineLive leadId={lead.id} initialEvents={events} />
        ) : (
          <>
            {/* Static fallback: tier=founding still sees the timeline,
                 just without realtime pushes — upgrade banner sits above. */}
            <div className="mb-3 flex items-center justify-between rounded-lg border border-dashed border-outline-variant bg-surface-container-low px-4 py-2.5 text-xs text-on-surface-variant">
              <span>
                Aggiornamento manuale — la{' '}
                <span className="font-semibold">timeline live</span> è
                disponibile con Pro.
              </span>
              <Link
                href="/settings#plan"
                className="group/link inline-flex items-center gap-1 font-semibold text-primary hover:underline"
              >
                Scopri
                <ArrowUpRight
                  size={12}
                  strokeWidth={2.5}
                  className="transition-transform group-hover/link:translate-x-0.5 group-hover/link:-translate-y-0.5"
                  aria-hidden
                />
              </Link>
            </div>
            {events.length === 0 ? (
              <p className="rounded-lg bg-surface-container-low p-6 text-sm text-on-surface-variant">
                Nessun evento ancora registrato per questo lead.
              </p>
            ) : (
              <ol className="space-y-1">
                {events.map((e) => (
                  <li
                    key={String(e.id)}
                    className="flex items-start gap-4 rounded-lg px-3 py-2 text-sm transition-colors hover:bg-surface-container-low"
                  >
                    <span className="w-28 shrink-0 text-xs text-on-surface-variant">
                      {relativeTime(e.occurred_at)}
                    </span>
                    <div className="flex-1">
                      <p className="font-semibold">{e.event_type}</p>
                      {e.event_source && (
                        <p className="text-xs text-on-surface-variant">
                          via {e.event_source}
                        </p>
                      )}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </>
        )}
      </BentoCard>

      {/* Risposte email (B.2) --------------------------------------------- */}
      <BentoCard span="full">
        <div className="mb-4">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Risposte email
          </p>
          <h2 className="font-headline text-2xl font-bold tracking-tighter">
            Messaggi ricevuti
          </h2>
          <p className="mt-1 text-sm text-on-surface-variant">
            Risposte del lead alle email di outreach, analizzate da AI con
            sentiment, intento e bozza di risposta.
          </p>
        </div>
        <LeadRepliesCard replies={replies} />
      </BentoCard>

      {/* WhatsApp conversations ------------------------------------------ */}
      <BentoCard span="full">
        <div className="mb-4">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            WhatsApp
          </p>
          <h2 className="font-headline text-2xl font-bold tracking-tighter">
            Conversazioni
          </h2>
          <p className="mt-1 text-sm text-on-surface-variant">
            Thread WhatsApp gestiti dall&apos;AI. Dopo {2} risposte automatiche
            o su richiesta del lead, l&apos;operatore subentra manualmente.
          </p>
        </div>
        <LeadConversationsCard
          leadId={lead.id}
          initialConversations={conversations}
        />
      </BentoCard>

      {/* GDPR — zona dati personali ---------------------------------------- */}
      <BentoCard span="full">
        <div className="mb-4">
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Zona GDPR
          </p>
          <h2 className="font-headline text-2xl font-bold tracking-tighter">
            Gestione dati personali
          </h2>
          <p className="mt-1 max-w-xl text-sm text-on-surface-variant">
            Esporta tutti i dati personali di questo lead (Art. 15) oppure
            eliminali definitivamente in risposta a un diritto all&apos;oblio
            (Art. 17). Ogni operazione viene registrata nel{' '}
            <Link
              href="/settings/privacy"
              className="font-semibold text-primary hover:underline"
            >
              log di audit
            </Link>
            .
          </p>
        </div>
        <LeadGdprActionsWrapper leadId={lead.id} leadName={name} />
      </BentoCard>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Presentational atoms
// ---------------------------------------------------------------------------

function DataCard({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <BentoCard padding="tight">
      <h2 className="mb-2 px-2 pt-2 font-headline text-lg font-bold tracking-tighter">
        {title}
      </h2>
      <dl className="space-y-0">{children}</dl>
    </BentoCard>
  );
}

function DataRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div
      className="flex items-center justify-between px-2 py-3 text-sm"
      style={{ boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }}
    >
      <dt className="text-on-surface-variant">{label}</dt>
      <dd className="text-right font-semibold text-on-surface">{value}</dd>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sequence helpers
// ---------------------------------------------------------------------------

/**
 * Engagement signals live on the lead, not the campaign — we attach
 * the open/click badge to the most recent *sent* step so the UI
 * doesn't falsely claim every step in the sequence was opened.
 */
function isLatestSent(
  c: { id: string; sent_at: string | null; sequence_step: number },
  all: { id: string; sent_at: string | null; sequence_step: number }[],
): boolean {
  const sent = all.filter((x) => x.sent_at);
  if (sent.length === 0) return false;
  const latest = sent.reduce((best, curr) =>
    (best.sent_at ?? '') > (curr.sent_at ?? '') ? best : curr,
  );
  return latest.id === c.id;
}
