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
  FileText,
  FolderOpen,
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
import { getLeadById, getLeadSectorSignal } from '@/lib/data/leads';
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
  const [lead, campaigns, events, replies, conversations, portalEvents, sectorSignal] =
    await Promise.all([
      getLeadById(id),
      listCampaignsForLead(id),
      listEventsForLead(id),
      getLeadReplies(id),
      getConversationsForLead(id),
      listPortalEventsForLead(id, 50),
      getLeadSectorSignal(id),
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
            {/* Preventivo entry-point. Disabled when the lead lacks the
                two prerequisites the AUTO bag needs (sized roof + ROI),
                so the editor never opens with a half-empty sidebar. */}
            <Link
              href={
                lead.roi_data && lead.roofs?.estimated_kwp
                  ? `/leads/${lead.id}/quote`
                  : '#'
              }
              aria-disabled={!lead.roi_data || !lead.roofs?.estimated_kwp}
              title={
                lead.roi_data && lead.roofs?.estimated_kwp
                  ? 'Genera un preventivo formale (PDF) per questo lead'
                  : 'Disponibile solo per lead con ROI e dimensionamento completati'
              }
              className={
                lead.roi_data && lead.roofs?.estimated_kwp
                  ? 'inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline'
                  : 'inline-flex cursor-not-allowed items-center gap-1 text-xs font-semibold text-on-surface-variant opacity-50'
              }
            >
              <FileText size={11} strokeWidth={2.25} aria-hidden />
              Genera preventivo completo
            </Link>
            {/* GSE practice entry-point. Sprint 1: visibile solo a contratto
                firmato (post-firma). La pagina /leads/{id}/practice/new
                fa il fetch del draft e gestisce sia "crea nuova" sia
                "apri esistente" (1 pratica per lead). */}
            {lead.feedback === 'contract_signed' && (
              <Link
                href={`/leads/${lead.id}/practice/new`}
                className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline"
                title="Crea pratica GSE post-firma (DM 37/08, Comunicazione Comune)"
              >
                <FolderOpen size={11} strokeWidth={2.25} aria-hidden />
                Crea pratica GSE
              </Link>
            )}
          </div>
        </div>

        {!isBlacklisted && (
          <SendOutreachButton leadId={lead.id} alreadySent={alreadySent} />
        )}
      </header>

      {/* ─── Hero: video simulazione + descrizione + KPI ───────────────
          Hero media fallback chain (video → GIF → static after image):
            1. ``rendering_video_url`` (Kling 1.6-Pro MP4) — full
               transition video, ideal hero.
            2. ``rendering_gif_url`` — same transition rendered as GIF
               for clients that can't autoplay video.
            3. ``rendering_image_url`` — the static "after" PNG with
               panels (nano-banana on Replicate, OR PIL geometric
               overlay when ``CREATIVE_SKIP_REPLICATE`` is on). When
               video + GIF both fail (Replicate quota exhausted,
               Remotion error, etc.) we still want a visual hero —
               the after-image is the same artefact the email body
               would use. Without this fallback the entire hero
               section was hidden, which made it look like the
               pipeline produced nothing visual.
          When ALL three are missing, the section is hidden entirely. */}
      {(lead.rendering_video_url ||
        lead.rendering_gif_url ||
        lead.rendering_image_url) ? (
        <section className="space-y-4 rounded-2xl bg-surface-container-low p-4 ring-1 ring-on-surface/5 shadow-ambient">
          <div className="overflow-hidden rounded-xl bg-black">
            {lead.rendering_video_url ? (
              // eslint-disable-next-line jsx-a11y/media-has-caption
              <video
                src={lead.rendering_video_url}
                poster={
                  lead.rendering_gif_url ??
                  lead.rendering_image_url ??
                  undefined
                }
                controls
                muted
                loop
                playsInline
                className="aspect-video w-full"
              />
            ) : lead.rendering_gif_url ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={lead.rendering_gif_url}
                alt="Simulazione fotovoltaico"
                className="aspect-video w-full object-cover"
              />
            ) : lead.rendering_image_url ? (
              // Static after-image fallback — Replicate or PIL panel
              // overlay on the real Solar API aerial. Same image the
              // email body uses when video render is bypassed.
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={lead.rendering_image_url}
                alt="Foto del tetto con pannelli (statica)"
                className="aspect-video w-full object-cover"
              />
            ) : null}
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 px-1">
            <p className="text-sm text-on-surface-variant">
              Simulazione di impianto fotovoltaico generata per questo lead.
              Lo stesso video è incluso nell&apos;email come hero cliccabile.
            </p>
            {publicLeadLink && (
              <a
                href={publicLeadLink}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 rounded-full bg-primary px-4 py-2 text-xs font-semibold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-90"
                title={publicLeadLink}
              >
                <ExternalLink size={12} strokeWidth={2.5} aria-hidden />
                Apri pagina personale del lead
              </a>
            )}
          </div>
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
          {lead.subjects?.sede_operativa_source && (
            <p className="px-1 text-[10px] uppercase tracking-widest text-on-surface-variant">
              Sede operativa ·{' '}
              <span className="font-semibold text-on-surface">
                {{
                  atoka: 'Atoka',
                  website_scrape: 'Sito web',
                  google_places: 'Google Places',
                  mapbox_hq: 'Centroide HQ',
                  manual: 'Manuale',
                  user_confirmed: 'Confermata da operatore',
                  vision: 'Claude Vision',
                  osm_snap: 'OSM building',
                }[lead.subjects.sede_operativa_source] ?? lead.subjects.sede_operativa_source}
              </span>
            </p>
          )}
        </section>
      ) : null}

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

      {/* ─── Cosa ha fatto sul portale ────────────────────────────────────
          Always-visible section (NOT a collapsible) so the operator
          sees portal engagement at a glance, right after the lead
          identity / roof block. Replaces the old "Cosa ha fatto sul
          portale" CollapsibleCard that was buried below the inviati /
          follow-up sections. The user explicitly asked: "non sia più
          un'attendina, ma una cosa ben disposta" — high-intent
          inbound signals deserve top-level real estate. */}
      <section className="space-y-3 rounded-2xl bg-surface-container-low p-5 ring-1 ring-on-surface/5">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Portale personale
            </p>
            <h2 className="font-headline text-lg font-bold text-on-surface">
              Cosa ha fatto sul portale
            </h2>
          </div>
          {(() => {
            // Combined activity count: portal_events (open / scroll /
            // bolletta upload / WhatsApp click / email click) PLUS
            // contact-form submissions (lead.appointment_requested in
            // events table) since those don't go to portal_events but
            // are the highest-intent inbound signal we have.
            const inquiryCount = events.filter(
              (e) => e.event_type === 'lead.appointment_requested',
            ).length;
            const total = portalEvents.length + inquiryCount;
            if (total === 0) return null;
            return (
              <span className="rounded-full bg-primary-container px-2.5 py-1 text-[10px] font-semibold uppercase tracking-widest text-on-primary-container">
                {total} {total === 1 ? 'azione' : 'azioni'}
              </span>
            );
          })()}
        </div>

        {/* Highlight: contact-form inquiries first — these are the
            single strongest intent signal in the entire funnel. The
            timeline below shows everything chronologically; this
            block surfaces the inquiry contents (name + phone +
            message) prominently above. */}
        {(() => {
          const inquiries = events.filter(
            (e) => e.event_type === 'lead.appointment_requested',
          );
          if (inquiries.length === 0) return null;
          return (
            <div className="rounded-xl bg-tertiary-container/40 p-4 ring-1 ring-tertiary/40">
              <p className="mb-2 inline-flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-widest text-on-tertiary-container">
                🔥 Richiesta di contatto
              </p>
              <ul className="space-y-2">
                {inquiries.map((iq) => {
                  const p = (iq.payload || {}) as Record<string, unknown>;
                  const contactName = p.contact_name as string | undefined;
                  const contactPhone = p.contact_phone as string | undefined;
                  const contactEmail = p.contact_email as string | undefined;
                  const message = p.message as string | undefined;
                  return (
                    <li
                      key={String(iq.id)}
                      className="rounded-lg bg-surface px-3 py-2 text-sm"
                    >
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                        {contactName && (
                          <strong className="text-on-surface">
                            {contactName}
                          </strong>
                        )}
                        {contactPhone && (
                          <a
                            href={`tel:${contactPhone}`}
                            className="text-primary hover:underline"
                          >
                            {contactPhone}
                          </a>
                        )}
                        {contactEmail && (
                          <a
                            href={`mailto:${contactEmail}`}
                            className="text-on-surface-variant hover:underline"
                          >
                            {contactEmail}
                          </a>
                        )}
                        <span className="text-[11px] text-on-surface-variant">
                          {relativeTime(iq.occurred_at)}
                        </span>
                      </div>
                      {message && (
                        <p className="mt-1 text-xs text-on-surface-variant">
                          {message}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })()}

        {/* Tracciamento azioni — portal_events timeline. Empty state
            shown when nothing has been recorded; the bolletta upload,
            scroll-90, WhatsApp/email click, etc. all surface here. */}
        {!hasPortalActivity ? (
          <p className="rounded-lg bg-surface px-4 py-3 text-sm text-on-surface-variant">
            Il lead non ha ancora visitato la pagina personale del portale.
          </p>
        ) : (
          <div className="rounded-lg bg-surface p-3">
            {lead.last_portal_event_at && (
              <p className="mb-3 text-[11px] text-on-surface-variant">
                Ultima attività {relativeTime(lead.last_portal_event_at)}
              </p>
            )}
            <LeadPortalTimeline events={portalEvents} />
          </div>
        )}
      </section>

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

      {/* ─── Breakdown punteggio ──────────────────────────────────────────
          Sprint 3.1 — explain WHY this lead has score X. The 5
          sub-scores (technical/consumption/incentives/solvency/
          distance) are written to leads.score_breakdown by ScoringAgent
          but were never surfaced. Operators couldn't tell the
          difference between a 48 (close to the threshold) and a 22
          (genuinely weak signal). */}
      {lead.score_breakdown &&
        typeof lead.score_breakdown === 'object' && (
          <CollapsibleCard
            label="Punteggio"
            title="Breakdown punteggio"
            badge={lead.score != null ? `${lead.score}/100` : undefined}
            defaultOpen={false}
          >
            <ScoreBreakdownGrid breakdown={lead.score_breakdown as Record<string, unknown>} />
            {sectorSignal && sectorSignal.predicted_sector ? (
              <SectorPredictionRow
                predictedSector={sectorSignal.predicted_sector}
                confidence={sectorSignal.sector_confidence}
                predictedAtecoCodes={sectorSignal.predicted_ateco_codes}
              />
            ) : null}
          </CollapsibleCard>
        )}

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

// ---------------------------------------------------------------------------
// ScoreBreakdownGrid — Sprint 3.1
//
// Renders the 5 sub-scores ScoringAgent persists on `leads.score_breakdown`
// as a horizontal bar chart so the operator can see at a glance which
// signals drove the final number. Each bar is 0..100; the labels are
// the canonical sub-score keys: technical, consumption, incentives,
// solvency, distance.
//
// We intentionally don't HARDCODE the sub-score names — we render
// whatever keys appear in the dict. ScoringAgent may add a 6th
// (e.g. ``intent_signals``) and this component picks it up
// automatically.
// ---------------------------------------------------------------------------

const SCORE_LABELS: Record<string, string> = {
  technical: 'Tecnico',
  consumption: 'Consumi',
  incentives: 'Incentivi',
  solvency: 'Solvibilità',
  distance: 'Distanza',
  intent_signals: 'Segnali intent',
};

function ScoreBreakdownGrid({
  breakdown,
}: {
  breakdown: Record<string, unknown>;
}) {
  const entries = Object.entries(breakdown)
    .filter(([, v]) => typeof v === 'number')
    .sort(([, a], [, b]) => (b as number) - (a as number));

  if (entries.length === 0) {
    return (
      <p className="text-sm text-on-surface-variant">
        Breakdown non disponibile per questo lead.
      </p>
    );
  }

  return (
    <div className="space-y-2 pt-1">
      {entries.map(([key, raw]) => {
        const value = Math.round(raw as number);
        const pct = Math.max(0, Math.min(100, value));
        return (
          <div key={key} className="flex items-center gap-3">
            <span className="w-32 shrink-0 text-xs text-on-surface-variant">
              {SCORE_LABELS[key] ?? key}
            </span>
            <div className="h-2 flex-1 rounded-full bg-surface-container-low">
              <div
                className="h-full rounded-full bg-primary"
                style={{ width: `${pct}%` }}
                aria-hidden
              />
            </div>
            <span className="w-10 text-right font-mono text-xs font-semibold text-on-surface">
              {value}
            </span>
          </div>
        );
      })}
      <p className="pt-1 text-[11px] text-on-surface-variant">
        Sub-score 0-100 calcolato da ScoringAgent. Il punteggio finale è la
        media pesata secondo i moduli configurati in /settings.
      </p>
    </div>
  );
}


// ---------------------------------------------------------------------------
// SectorPredictionRow — Sprint C.3
// ---------------------------------------------------------------------------
//
// Renders the sector-aware tag the hunter funnel stamped on this lead's
// `scan_candidates` row (predicted_sector, sector_confidence, and the
// Haiku-validated predicted_ateco_codes from L3). Hidden entirely when
// the lead pre-dates the sector-aware rollout (no row in scan_candidates
// or null predicted_sector).
//
// The labels mirror the curated `_DISPLAY_NAMES` in the API
// `/v1/sectors/wizard-groups` endpoint — hardcoded here to keep the
// dashboard server-side rendering free of the extra fetch.

const SECTOR_LABELS: Record<string, string> = {
  industry_heavy: 'Manifatturiero pesante',
  industry_light: 'Manifatturiero leggero',
  food_production: 'Produzione alimentare',
  logistics: 'Logistica e magazzinaggio',
  retail_gdo: 'Grande distribuzione',
  horeca: 'Ristorazione e bar',
  hospitality_large: 'Ricettivo grande',
  hospitality_food_service: 'Ristorazione collettiva',
  healthcare: 'Sanitario',
  healthcare_private: 'Sanitario privato',
  agricultural_intensive: 'Agricolo intensivo',
  automotive: 'Automotive',
  education: 'Istruzione',
  personal_services: 'Servizi alla persona',
  professional_offices: 'Uffici professionali',
};

function SectorPredictionRow({
  predictedSector,
  confidence,
  predictedAtecoCodes,
}: {
  predictedSector: string;
  confidence: number | null;
  predictedAtecoCodes: string[];
}) {
  const label = SECTOR_LABELS[predictedSector] ?? predictedSector;
  const confidencePct =
    confidence != null && Number.isFinite(confidence)
      ? Math.round(confidence * 100)
      : null;
  return (
    <div className="mt-3 border-t border-outline-variant pt-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-on-surface-variant">Settore predetto:</span>
        <span className="rounded-full bg-primary-container px-2 py-0.5 font-semibold text-on-primary-container">
          {label}
        </span>
        {confidencePct !== null ? (
          <span className="text-on-surface-variant">
            confidence {confidencePct}%
          </span>
        ) : null}
        {predictedAtecoCodes.length > 0 ? (
          <span
            className="text-on-surface-variant"
            title={predictedAtecoCodes.join(', ')}
          >
            · ATECO suggeriti: {predictedAtecoCodes.slice(0, 3).join(', ')}
            {predictedAtecoCodes.length > 3 ? '…' : ''}
          </span>
        ) : null}
      </div>
      <p className="mt-1 text-[11px] text-on-surface-variant">
        Stampato da L1 (ATECO + nome) e raffinato da Haiku in L3. Confidence
        1.0 = match esatto sull&apos;ATECO seedato; 0.7 = match per prefisso
        2 cifre; 0.4 = match fuzzy sulla ragione sociale.
      </p>
    </div>
  );
}
