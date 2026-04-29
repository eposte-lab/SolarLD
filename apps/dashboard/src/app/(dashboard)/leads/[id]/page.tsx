/**
 * Scheda lead — layout verticale con sezioni a tendina.
 *
 * Struttura:
 *   1. Header — nome, stato, score, azioni
 *   2. Rendering impianto (sempre visibile se presente)
 *   3. Dati preventivo (4 KPI)
 *   4. Anagrafica + Tetto (griglia 2 col)
 *   5. [Tendina] Comunicazioni inviate
 *   6. [Tendina] Scrivi follow-up (AI)
 *   7. [Tendina] Attività sul portale
 *   8. [Tendina] Storico eventi
 *   9. [Tendina] Risposte ricevute
 *  10. [Tendina] Conversazione WhatsApp
 *  11. [Tendina] Dati tecnici impianto (analisi satellitare)
 *  12. [Tendina] Privacy e GDPR
 */

import {
  AlertTriangle,
  ArrowLeft,
  ArrowUpRight,
  Check,
  ExternalLink,
  Phone,
} from 'lucide-react';
import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';

import { FollowUpDrafter } from '@/components/follow-up-drafter';
import { LeadConversationsCard } from '@/components/lead-conversations-card';
import { LeadRepliesCard } from '@/components/lead-replies-card';
import { LeadPortalTimeline } from '@/components/lead-portal-timeline';
import { LeadTimelineLive } from '@/components/lead-timeline-live';
import { LeadGdprActionsWrapper } from './LeadGdprActionsWrapper';
import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { CollapsibleCard } from '@/components/ui/collapsible-card';
import { GlassPanel } from '@/components/ui/glass-panel';
import { KpiChipCard } from '@/components/ui/kpi-chip-card';
import { EngagementScoreChip } from '@/components/ui/engagement-score-chip';
import { StatusChip, TierChip } from '@/components/ui/status-chip';
import { TierLock } from '@/components/ui/tier-lock';
import { listCampaignsForLead, listEventsForLead } from '@/lib/data/campaigns';
import { listPortalEventsForLead } from '@/lib/data/engagement';
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
import { cn } from '@/lib/utils';

import { SendOutreachButton } from './SendOutreachButton';
import { SolarApiInspector } from './SolarApiInspector';

export const dynamic = 'force-dynamic';

type PageProps = { params: Promise<{ id: string }> };

// ---------------------------------------------------------------------------
// Etichette italiane per event_type tecnici
// ---------------------------------------------------------------------------

const EVENT_LABELS: Record<string, string> = {
  'lead.created':               'Lead creato',
  'lead.scored':                'Punteggio calcolato',
  'lead.rendered':              'Rendering impianto generato',
  'lead.render_skipped':        'Rendering saltato',
  'lead.outreach_sent':         'Email outreach inviata',
  'lead.outreach_skipped':      'Invio saltato',
  'lead.outreach_skipped_tier': 'Invio bloccato (piano)',
  'lead.outreach_ratelimited':  'Invio rinviato (limite giornaliero)',
  'lead.followup_sent_step2':   'Follow-up giorno 4 inviato',
  'lead.followup_sent_step3':   'Follow-up giorno 9 inviato',
  'lead.followup_sent_step4':   'Follow-up finale inviato',
  'lead.followup_sent_step5':   'Follow-up automatico inviato',
  'lead.followup_sent_step6':   'Follow-up automatico inviato',
  'lead.followup_sent_step7':   'Follow-up automatico inviato',
  'lead.followup_sent_step8':   'Follow-up automatico inviato',
  'lead.followup_sent_step9':   'Follow-up automatico inviato',
  'lead.follow_up_sent':        'Follow-up manuale inviato',
  'lead.email_delivered':       'Email consegnata',
  'lead.email_opened':          'Email aperta',
  'lead.email_clicked':         'Link cliccato',
  'lead.email_bounced':         'Email respinta (bounce)',
  'lead.email_complained':      'Segnalazione spam',
  'lead.postal_sent':           'Lettera postale inviata',
  'lead.postal_delivered':      'Lettera consegnata',
  'lead.postal_unknown':        'Evento postale',
  'lead.portal_visited':        'Pagina personale aperta',
  'lead.whatsapp_click':        'WhatsApp cliccato',
  'lead.appointment_requested': 'Appuntamento richiesto',
  'lead.bolletta_uploaded':     'Bolletta caricata',
  'lead.conversion_recorded':   'Contratto firmato',
  'lead.optout_requested':      'Disiscrizione richiesta',
  'lead.feedback_updated':      'Nota operatore aggiornata',
  'lead.contacted':             'Contatto registrato',
  'lead.deleted':               'Lead eliminato',
  'lead.engaged':               'Lead engaged',
  'lead.contract_signed':       'Contratto firmato',
};

function labelForEvent(type: string): string {
  if (EVENT_LABELS[type]) return EVENT_LABELS[type];
  // generic fallback: strip "lead." prefix, replace _ with spaces
  return type.replace(/^lead\./, '').replace(/_/g, ' ');
}

// ---------------------------------------------------------------------------
// Etichette canali e step
// ---------------------------------------------------------------------------

const CHANNEL_LABELS: Record<string, string> = {
  email:    'Email',
  postal:   'Lettera',
  whatsapp: 'WhatsApp',
};

const STEP_LABELS: Record<number, string> = {
  1: 'Primo contatto',
  2: 'Promemoria (4gg)',
  3: 'Approfondimento (9gg)',
  4: 'Messaggio finale (14gg)',
  5: 'Follow-up automatico',
  6: 'Follow-up automatico',
  7: 'Follow-up automatico',
  8: 'Follow-up automatico',
  9: 'Follow-up automatico',
};

// ---------------------------------------------------------------------------
// Pagina
// ---------------------------------------------------------------------------

export default async function LeadDetailPage({ params }: PageProps) {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const { id } = await params;
  const [lead, campaigns, events, replies, conversations, portalEvents] =
    await Promise.all([
      getLeadById(id),
      listCampaignsForLead(id),
      listEventsForLead(id),
      getLeadReplies(id),
      getConversationsForLead(id),
      listPortalEventsForLead(id, 50),
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
  // NEXT_PUBLIC_LEAD_PORTAL_URL must point at the SEPARATE lead-portal
  // Vercel project, not the dashboard. Any trailing slash is normalised
  // here so a sloppy env value still produces a valid URL. We also bail
  // to '#' when the lead row has no slug yet, instead of building
  // `/lead/null` which 404s on the portal.
  const portalUrl = (
    process.env.NEXT_PUBLIC_LEAD_PORTAL_URL || 'http://localhost:3001'
  ).replace(/\/+$/, '');
  const publicLeadLink = lead.public_slug
    ? `${portalUrl}/lead/${lead.public_slug}`
    : '#';
  const alreadySent = lead.outreach_sent_at != null;

  const sentCampaigns = campaigns.filter((c) => c.sent_at);
  const hasPortalActivity = portalEvents.length > 0;
  const hasReplies = replies.length > 0;
  const hasConversations = conversations.length > 0;

  return (
    <div className="space-y-4">
      {/* ─── Header ───────────────────────────────────────────────────── */}
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
              Punteggio{' '}
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
              Pagina personale
              <ExternalLink size={11} strokeWidth={2.25} aria-hidden />
            </a>
          </div>
        </div>

        {!isBlacklisted && (
          <SendOutreachButton leadId={lead.id} alreadySent={alreadySent} />
        )}
      </header>

      {/* ─── Rendering impianto ───────────────────────────────────────── */}
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
          {/*
            Sede operativa provenance badge — surfaces which tier of
            the cascade produced the rooftop coords. "Centroide HQ"
            (mapbox_hq) signals to ops that the render likely sits on
            an industrial-cluster centroid rather than the actual
            building, and the lead deserves a manual address upgrade
            before sending.
          */}
          {lead.subjects?.sede_operativa_source && (
            <GlassPanel className="absolute right-5 top-5 px-3 py-1.5">
              <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                Sede operativa
              </p>
              <p className="text-xs font-bold text-on-surface">
                {{
                  atoka: 'Atoka',
                  website_scrape: 'Sito web',
                  google_places: 'Google Places',
                  mapbox_hq: 'Centroide HQ',
                  manual: 'Manuale',
                }[lead.subjects.sede_operativa_source] ?? '—'}
              </p>
            </GlassPanel>
          )}
        </div>
      )}

      {/* ─── Video / GIF ──────────────────────────────────────────────── */}
      {(lead.rendering_video_url || lead.rendering_gif_url) && (
        <BentoCard title="Anteprima video" padding="tight" span="full">
          <div className="flex flex-col gap-4 p-4 sm:flex-row sm:items-start">
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
                Simulazione impianto fotovoltaico generata per questo lead.
                Il video viene incluso nell&apos;email come hero cliccabile.
              </p>
              {lead.portal_video_slug && (
                <a
                  href={`${portalUrl}/lead/${lead.portal_video_slug}/video`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-on-primary transition-opacity hover:opacity-90"
                >
                  Apri landing video
                </a>
              )}
            </div>
          </div>
        </BentoCard>
      )}

      {/* ─── KPI preventivo ───────────────────────────────────────────── */}
      <BentoGrid cols={4}>
        <KpiChipCard
          label="Potenza impianto"
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

      {/* ─── Anagrafica + Tetto ───────────────────────────────────────── */}
      <BentoGrid cols={2}>
        <DataCard title="Anagrafica">
          <DataRow
            label="Tipo cliente"
            value={lead.subjects?.type === 'b2b' ? 'Azienda' : lead.subjects?.type === 'b2c' ? 'Privato' : (lead.subjects?.type?.toUpperCase() ?? '—')}
          />
          <DataRow
            label="Ragione sociale"
            value={lead.subjects?.business_name ?? '—'}
          />
          <DataRow
            label="Referente"
            value={
              [lead.subjects?.owner_first_name, lead.subjects?.owner_last_name]
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
          {/*
            Telefono — populated by L2 enrichment from Atoka raw payload
            (`raw.phones`/`raw.contacts`/`raw.base.phone`, free in the
            includeContacts bundle), with website-scrape fallback when
            Atoka has nothing. Source badge ("Atoka", "Sito web",
            "Manuale") so ops can audit data quality at a glance.
            Wrapped in a <a href="tel:"> for one-tap dial on mobile.
          */}
          <DataRow
            label="Telefono"
            value={
              lead.subjects?.decision_maker_phone ? (
                <span className="inline-flex items-center justify-end gap-1.5">
                  <a
                    href={`tel:${lead.subjects.decision_maker_phone}`}
                    className="hover:underline focus:underline focus:outline-none"
                  >
                    {lead.subjects.decision_maker_phone}
                  </a>
                  <Phone
                    size={12}
                    strokeWidth={2.25}
                    className="text-on-surface-variant"
                    aria-hidden
                  />
                  {lead.subjects.decision_maker_phone_source ? (
                    <span
                      className="rounded-full bg-surface-container-low px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-on-surface-variant"
                      title="Sorgente del numero di telefono"
                    >
                      {lead.subjects.decision_maker_phone_source === 'atoka'
                        ? 'Atoka'
                        : lead.subjects.decision_maker_phone_source ===
                            'website_scrape'
                          ? 'Sito web'
                          : 'Manuale'}
                    </span>
                  ) : null}
                </span>
              ) : (
                '—'
              )
            }
          />
          {/*
            Enrichment fields — populated by Atoka in production and by
            `demo_mock_enrichment` for the demo "Avvia test pipeline".
            Each row gracefully renders "—" when the underlying value
            is null, so a sparsely-enriched lead doesn't break layout.
          */}
          <DataRow
            label="Ruolo"
            value={lead.subjects?.decision_maker_role ?? '—'}
          />
          <DataRow
            label="ATECO"
            value={
              lead.subjects?.ateco_code || lead.subjects?.ateco_description ? (
                <span className="inline-flex flex-wrap items-center justify-end gap-1.5">
                  {lead.subjects?.ateco_code && (
                    <span className="rounded bg-surface-container-low px-1.5 py-0.5 font-mono text-[11px]">
                      {lead.subjects.ateco_code}
                    </span>
                  )}
                  {lead.subjects?.ateco_description && (
                    <span className="text-on-surface">
                      {lead.subjects.ateco_description}
                    </span>
                  )}
                </span>
              ) : (
                '—'
              )
            }
          />
          <DataRow
            label="Fatturato annuo"
            value={
              lead.subjects?.yearly_revenue_cents != null
                ? formatEurPlain(lead.subjects.yearly_revenue_cents / 100)
                : '—'
            }
          />
          <DataRow
            label="Dipendenti"
            value={
              lead.subjects?.employees != null
                ? formatNumber(lead.subjects.employees)
                : '—'
            }
          />
          <DataRow
            label="LinkedIn"
            value={
              lead.subjects?.linkedin_url ? (
                <a
                  href={lead.subjects.linkedin_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-primary hover:underline"
                >
                  Profilo aziendale
                  <ExternalLink size={11} strokeWidth={2.25} aria-hidden />
                </a>
              ) : (
                '—'
              )
            }
          />
        </DataCard>

        <DataCard title="Tetto e impianto">
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
            label="Produzione stimata"
            value={
              lead.roofs?.estimated_yearly_kwh
                ? `${formatNumber(lead.roofs.estimated_yearly_kwh)} kWh/anno`
                : '—'
            }
          />
        </DataCard>
      </BentoGrid>

      {/* ─── Email e comunicazioni inviate ────────────────────────────── */}
      <CollapsibleCard
        label="Comunicazioni"
        title="Email e messaggi inviati"
        badge={sentCampaigns.length > 0 ? `${sentCampaigns.length} invii` : undefined}
        defaultOpen={sentCampaigns.length > 0}
      >
        {campaigns.length === 0 ? (
          <div className="rounded-lg bg-surface-container-low p-5 text-sm text-on-surface-variant">
            Nessuna comunicazione ancora. Premi{' '}
            <em className="font-semibold text-primary">Invia outreach</em> per
            avviare il primo contatto.
          </div>
        ) : (
          <ul className="space-y-2 pt-1">
            {campaigns.map((c) => (
              <li
                key={c.id}
                className="flex items-center justify-between rounded-lg bg-surface-container-low px-5 py-3 text-sm"
              >
                <div className="space-y-0.5">
                  <p className="font-semibold">
                    {STEP_LABELS[c.sequence_step] ?? `Messaggio ${c.sequence_step}`}
                    {' · '}
                    <span className="text-[10px] uppercase tracking-widest text-on-surface-variant">
                      {CHANNEL_LABELS[c.channel] ?? c.channel}
                    </span>
                  </p>
                  {c.email_subject && (
                    <p className="text-xs text-on-surface-variant">
                      Oggetto: {c.email_subject}
                    </p>
                  )}
                </div>
                <div className="flex items-center gap-3 text-xs text-on-surface-variant">
                  {c.sent_at && <span>{relativeTime(c.sent_at)}</span>}
                  {isLatestSent(c, campaigns) && lead.outreach_opened_at && (
                    <span className="font-semibold text-primary">Aperto</span>
                  )}
                  {isLatestSent(c, campaigns) && lead.outreach_clicked_at && (
                    <span className="font-semibold text-primary">Click</span>
                  )}
                  {c.status === 'failed' && (
                    <span className="font-semibold text-error">
                      Non consegnata{c.failure_reason ? ` · ${c.failure_reason}` : ''}
                    </span>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}
      </CollapsibleCard>

      {/* ─── Scrivi follow-up con AI ──────────────────────────────────── */}
      {!isBlacklisted && (
        <CollapsibleCard
          label="Follow-up assistito"
          title="Scrivi con AI"
          defaultOpen={false}
        >
          <p className="mb-4 text-sm text-on-surface-variant">
            L&apos;AI analizza il preventivo, il comportamento sul portale e le
            comunicazioni precedenti, e scrive una bozza su misura.
            Puoi modificarla prima di inviarla.
          </p>
          <TierLock
            feature="advanced_analytics"
            tenant={ctx.tenant}
            featureLabel="Follow-up con AI"
            inline
          >
            <FollowUpDrafter leadId={lead.id} />
          </TierLock>
        </CollapsibleCard>
      )}

      {/* ─── Attività sul portale ─────────────────────────────────────── */}
      <CollapsibleCard
        label="Portale personale"
        title="Cosa ha fatto sul portale"
        badge={
          hasPortalActivity
            ? `${portalEvents.length} ${portalEvents.length === 1 ? 'azione' : 'azioni'}`
            : undefined
        }
        defaultOpen={hasPortalActivity}
      >
        {!hasPortalActivity ? (
          <p className="pt-1 text-sm text-on-surface-variant">
            Il lead non ha ancora visitato la pagina personale.
          </p>
        ) : (
          <>
            {lead.last_portal_event_at && (
              <p className="mb-3 text-xs text-on-surface-variant">
                Ultima attività {relativeTime(lead.last_portal_event_at)}
              </p>
            )}
            <LeadPortalTimeline events={portalEvents} />
          </>
        )}
      </CollapsibleCard>

      {/* ─── Storico eventi ───────────────────────────────────────────── */}
      <CollapsibleCard
        label="Cronologia"
        title="Storico eventi"
        badge={events.length > 0 ? `${events.length} eventi` : undefined}
        defaultOpen={false}
      >
        {canTenantUse(ctx.tenant, 'realtime_timeline') ? (
          <div className="pt-1">
            <div className="mb-3 inline-flex items-center gap-1.5 rounded-full bg-primary-container px-2.5 py-1 text-[10px] font-semibold uppercase tracking-widest text-on-primary-container">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
              </span>
              Aggiornamento live
            </div>
            <LeadTimelineLive leadId={lead.id} initialEvents={events} />
          </div>
        ) : (
          <div className="pt-1">
            {events.length === 0 ? (
              <p className="text-sm text-on-surface-variant">
                Nessun evento ancora registrato.
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
                      <p className="font-medium text-on-surface">
                        {labelForEvent(e.event_type)}
                      </p>
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
          </div>
        )}
      </CollapsibleCard>

      {/* ─── Risposte email ricevute ───────────────────────────────────── */}
      <CollapsibleCard
        label="Risposte"
        title="Messaggi ricevuti"
        badge={hasReplies ? `${replies.length}` : undefined}
        defaultOpen={hasReplies}
      >
        <div className="pt-1">
          {!hasReplies ? (
            <p className="text-sm text-on-surface-variant">
              Nessuna risposta ricevuta ancora.
            </p>
          ) : (
            <LeadRepliesCard replies={replies} />
          )}
        </div>
      </CollapsibleCard>

      {/* ─── WhatsApp ─────────────────────────────────────────────────── */}
      <CollapsibleCard
        label="WhatsApp"
        title="Conversazione WhatsApp"
        badge={hasConversations ? `${conversations.length}` : undefined}
        defaultOpen={hasConversations}
      >
        <div className="pt-1">
          {!hasConversations ? (
            <p className="text-sm text-on-surface-variant">
              Nessuna conversazione WhatsApp ancora.
            </p>
          ) : (
            <LeadConversationsCard
              leadId={lead.id}
              initialConversations={conversations}
            />
          )}
        </div>
      </CollapsibleCard>

      {/* ─── Dati tecnici impianto (analisi satellitare) ───────────────── */}
      <CollapsibleCard
        label="Dati tecnici"
        title="Dettagli impianto"
        defaultOpen={false}
      >
        <div className="pt-1">
          <SolarApiInspector lead={lead} />
        </div>
      </CollapsibleCard>

      {/* ─── Privacy e GDPR ───────────────────────────────────────────── */}
      <CollapsibleCard
        label="Privacy"
        title="Dati personali e GDPR"
        defaultOpen={false}
      >
        <p className="mb-4 text-sm text-on-surface-variant">
          Esporta tutti i dati personali (Art. 15) oppure eliminali su
          richiesta del cliente (Art. 17). Ogni azione viene registrata nel{' '}
          <Link
            href="/settings/privacy"
            className="font-semibold text-primary hover:underline"
          >
            log di audit
          </Link>
          .
        </p>
        <LeadGdprActionsWrapper leadId={lead.id} leadName={name} />
      </CollapsibleCard>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Componenti presentazionali
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
// Helpers
// ---------------------------------------------------------------------------

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
