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
  ArrowLeft,
  ArrowUpRight,
  ExternalLink,
  FileText,
  FolderOpen,
  Mail,
} from 'lucide-react';
import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';

import { FollowUpDrafter } from '@/components/follow-up-drafter';
import { LeadActivityStrip } from '@/components/lead-activity-strip';
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
import { FollowUpStateChip } from '@/components/ui/follow-up-state-chip';
import { StatusChip } from '@/components/ui/status-chip';
import { TierLock } from '@/components/ui/tier-lock';
import { listCampaignsForLead, listEventsForLead } from '@/lib/data/campaigns';
import { getPortalSessionStats, listPortalEventsForLead } from '@/lib/data/engagement';
import { getLeadById, getLeadSectorSignal, getLeadV3Signal } from '@/lib/data/leads';
import type { LeadV3Signal } from '@/lib/data/leads';
import { getConversationsForLead } from '@/lib/data/conversations';
import { getLeadReplies } from '@/lib/data/replies';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { canTenantUse } from '@/lib/data/tier';
import {
  formatDate,
  formatDuration,
  formatEurPlain,
  formatNumber,
  relativeTime,
} from '@/lib/utils';
import { cn } from '@/lib/utils';

import { LeadFeedbackPicker } from './LeadFeedbackPicker';
import { RegenerateRenderingButton } from './RegenerateRenderingButton';
import { SendOutreachButton } from './SendOutreachButton';
import { SendTestOutreachForm } from './SendTestOutreachForm';
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

  // Each fetcher can fail independently for v3 leads (schema drift, missing
  // joined column, etc.). Surface the first failure as a render-able error
  // payload so we don't lose the message to Next.js production digest masking.
  // Once the v3 funnel ↔ dashboard contract stabilises this can be replaced
  // by a Promise.all again.
  type FetchOk<T> = { ok: true; value: T };
  type FetchErr = { ok: false; source: string; message: string; stack?: string };
  const wrap = async <T,>(
    source: string,
    p: Promise<T>,
  ): Promise<FetchOk<T> | FetchErr> => {
    try {
      return { ok: true, value: await p };
    } catch (e) {
      const err = e as Error;
      return {
        ok: false,
        source,
        message: err?.message ?? String(e),
        stack: err?.stack,
      };
    }
  };

  const [
    leadR,
    campaignsR,
    eventsR,
    repliesR,
    conversationsR,
    portalEventsR,
    portalStatsR,
    sectorSignalR,
    v3SignalR,
  ] = await Promise.all([
    wrap('getLeadById', getLeadById(id)),
    wrap('listCampaignsForLead', listCampaignsForLead(id)),
    wrap('listEventsForLead', listEventsForLead(id)),
    wrap('getLeadReplies', getLeadReplies(id)),
    wrap('getConversationsForLead', getConversationsForLead(id)),
    wrap('listPortalEventsForLead', listPortalEventsForLead(id, 50)),
    wrap('getPortalSessionStats', getPortalSessionStats(id)),
    wrap('getLeadSectorSignal', getLeadSectorSignal(id)),
    wrap('getLeadV3Signal', getLeadV3Signal(id).catch(() => null)),
  ]);

  const errors = [
    leadR, campaignsR, eventsR, repliesR, conversationsR,
    portalEventsR, portalStatsR, sectorSignalR, v3SignalR,
  ].filter((r): r is FetchErr => !r.ok);

  if (errors.length > 0) {
    return (
      <div className="space-y-4 p-6">
        <h1 className="font-headline text-2xl font-bold tracking-tighter">
          Errore caricamento lead
        </h1>
        <p className="text-sm text-on-surface-variant">
          La pagina ha incontrato {errors.length} errore/i durante il fetch dei
          dati. Dettagli sotto.
        </p>
        {errors.map((e, i) => (
          <div
            key={i}
            className="space-y-2 rounded-lg bg-error-container/40 p-4 text-sm"
          >
            <div>
              <span className="font-semibold">Source:</span>{' '}
              <code className="rounded bg-surface-container-low px-1.5 py-0.5 font-mono text-xs">
                {e.source}
              </code>
            </div>
            <div>
              <span className="font-semibold">Message:</span>{' '}
              <code className="rounded bg-surface-container-low px-1.5 py-0.5 font-mono text-xs">
                {e.message}
              </code>
            </div>
            {e.stack && (
              <details>
                <summary className="cursor-pointer font-semibold">
                  Stack trace
                </summary>
                <pre className="mt-2 overflow-x-auto rounded bg-surface-container-low p-3 text-[11px] leading-snug">
                  {e.stack}
                </pre>
              </details>
            )}
          </div>
        ))}
      </div>
    );
  }

  const lead = leadR.ok ? leadR.value : null;
  const campaigns = campaignsR.ok ? campaignsR.value : [];
  const events = eventsR.ok ? eventsR.value : [];
  const replies = repliesR.ok ? repliesR.value : [];
  const conversations = conversationsR.ok ? conversationsR.value : [];
  const portalEvents = portalEventsR.ok ? portalEventsR.value : [];
  const portalStats = portalStatsR.ok
    ? portalStatsR.value
    : { sessions: 0, total_time_sec: 0, deepest_scroll_pct: 0, last_event_at: null, is_live_now: false };
  const sectorSignal = sectorSignalR.ok ? sectorSignalR.value : null;
  const v3Signal = v3SignalR.ok ? v3SignalR.value : null;

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

  // ─── Display fallbacks (v3) ──────────────────────────────────────
  // For freshly-promoted v3 leads the legacy subjects.* / roofs.*
  // columns are often NULL. The scan_candidates row already has the
  // data from Google Places + L2 scraping + Haiku — pull it via
  // v3Signal so the Anagrafica/Tetto cards aren't a wall of "—".
  // The DataRow component auto-hides when value is null/'' so any
  // residual unfilled rows simply disappear.
  const dispRagioneSociale =
    lead.subjects?.business_name ?? v3Signal?.display_name ?? null;
  const dispEmail =
    lead.subjects?.decision_maker_email ?? v3Signal?.best_email ?? null;
  const dispPhone =
    lead.subjects?.decision_maker_phone ?? v3Signal?.best_phone ?? null;
  // ATECO: subjects has both code + description; v3 only has codes
  // (predicted by Haiku). When falling back, show only the code chip.
  const dispAtecoCode =
    lead.subjects?.ateco_code ?? v3Signal?.predicted_ateco_codes?.[0] ?? null;
  const dispAtecoDescription = lead.subjects?.ateco_description ?? null;
  // Tetto e impianto fallbacks
  const dispTettoIndirizzo = address || v3Signal?.formatted_address || null;
  const dispProvincia = (() => {
    if (lead.roofs?.provincia) return lead.roofs.provincia;
    // Parse "PROV" from the tail of formatted_address Google Places returns
    // (e.g. "Via X 1, 80100 Napoli NA, Italia" → "NA"). Same heuristic as
    // displayProvince() in lib/contatti-display.ts.
    const fa = v3Signal?.formatted_address ?? null;
    if (!fa) return null;
    const parts = fa.split(',').map((s) => s.trim());
    const cityCap = parts.length >= 2 ? parts[parts.length - 2] : null;
    const m = cityCap?.match(/\s([A-Z]{2})$/);
    return m?.[1] ?? null;
  })();
  const isBlacklisted = lead.pipeline_status === 'blacklisted';
  // Generic-outreach leads were promoted from a custom campaign list
  // (no Solar API call). They have a placeholder roof with data_source
  // 'places_only' — no rendering, no kWp/ROI data, no preventivo.
  const isGenericOutreach = lead.roofs?.data_source === 'places_only';
  // NEXT_PUBLIC_LEAD_PORTAL_URL must point at the SEPARATE lead-portal
  // Vercel project, not the dashboard. We aggressively normalise the env
  // value:
  //   * strip any path / query / hash that crept in (e.g. someone pasted
  //     a `?_vercel_share=…` deployment-protection bypass URL — that
  //     token would otherwise eat the /lead/<slug> suffix and the portal
  //     would just show the welcome screen).
  //   * strip trailing slashes.
  // Bail to '#' when the lead row has no slug yet, instead of building
  // `/lead/null` which 404s on the portal.
  const rawPortalEnv =
    process.env.NEXT_PUBLIC_LEAD_PORTAL_URL || 'http://localhost:3001';
  let portalUrl: string;
  try {
    const u = new URL(rawPortalEnv);
    portalUrl = `${u.protocol}//${u.host}`;
  } catch {
    portalUrl = rawPortalEnv.replace(/\/+$/, '');
  }
  const publicLeadLink = lead.public_slug
    ? `${portalUrl}/lead/${lead.public_slug}`
    : '#';
  const alreadySent = lead.outreach_sent_at != null;

  const sentCampaigns = campaigns.filter((c) => c.sent_at);
  const hasPortalActivity = portalEvents.length > 0;
  const hasReplies = replies.length > 0;
  const hasConversations = conversations.length > 0;

  // Derive bolletta/appointment timestamps from the events stream — they
  // don't have dedicated columns on the lead row. The activity strip in
  // the header reads these so the operator sees the full status at a
  // glance instead of having to scroll the timeline.
  const latestEventAt = (
    types: ReadonlyArray<string>,
  ): string | null => {
    let latest: string | null = null;
    for (const e of events) {
      if (!types.includes(e.event_type)) continue;
      if (!e.occurred_at) continue;
      if (!latest || e.occurred_at > latest) latest = e.occurred_at;
    }
    return latest;
  };
  const activityFlags = {
    outreachSentAt: lead.outreach_sent_at,
    outreachOpenedAt: lead.outreach_opened_at,
    outreachClickedAt: lead.outreach_clicked_at,
    portalVisitedAt:
      lead.last_portal_event_at ?? lead.dashboard_visited_at ?? null,
    bollettaUploadedAt: latestEventAt(['lead.bolletta_uploaded']),
    appointmentRequestedAt: latestEventAt(['lead.appointment_requested']),
  };

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
          {/* Activity-at-a-glance — the operator's mental model is
              "did the lead read the email? click? visit the portal?"
              Render this *first* so the answer is the first thing the
              eye lands on, before the technical chips below. */}
          <LeadActivityStrip flags={activityFlags} />
          <div className="flex flex-wrap items-center gap-2">
            {/* The header shows the lead's *current* state: pipeline status
                + engagement (real portal activity over the last 30d). The
                old `score_tier` cold/warm/hot was a pre-engagement ICP
                prediction (revenue/employees/sector) — leaving it here
                next to engagement=100 produced "this lead is cold" /
                "this lead is at 100" contradictions. The ICP tier still
                lives in the Score breakdown card below, where its
                provenance is explained. */}
            <EngagementScoreChip
              score={lead.engagement_score}
              updatedAt={lead.engagement_score_updated_at}
            />
            <FollowUpStateChip row={lead} />
            <StatusChip status={lead.pipeline_status} />
            <span
              className="text-xs text-on-surface-variant"
              title="Score ICP iniziale (settore, dimensione, distanza). Resta fisso dopo l'import — l'engagement qui sopra invece riflette l'attività reale."
            >
              ICP{' '}
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
            {/* Preventivo entry-point. Hidden for generic_outreach leads
                (no Solar data — preventivo would be empty/meaningless).
                Disabled for Solar leads that lack the two prerequisites. */}
            {!isGenericOutreach && (
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
            )}
            {/* GSE practice entry-point. Visible always so the operator
                knows the feature exists, but disabled until the lead is
                marked contract_signed via the LeadFeedbackPicker below. */}
            {lead.feedback === 'contract_signed' ? (
              <Link
                href={`/leads/${lead.id}/practice/new`}
                className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline"
                title="Crea pratica GSE post-firma (DM 37/08, Comunicazione Comune)"
              >
                <FolderOpen size={11} strokeWidth={2.25} aria-hidden />
                Crea pratica GSE
              </Link>
            ) : (
              <span
                className="inline-flex cursor-not-allowed items-center gap-1 text-xs font-semibold text-on-surface-variant opacity-50"
                title="Marca il lead come 'Contratto firmato' qui sotto per abilitare la creazione della pratica GSE"
              >
                <FolderOpen size={11} strokeWidth={2.25} aria-hidden />
                Crea pratica GSE
              </span>
            )}
            <LeadFeedbackPicker
              leadId={lead.id}
              currentFeedback={lead.feedback ?? null}
            />
          </div>
        </div>

        {!isBlacklisted && !ctx.tenant.outreach_blocked && (
          <SendOutreachButton leadId={lead.id} alreadySent={alreadySent} />
        )}
      </header>

      {/* Demo-mode: replace the regular send button with a form that
          accepts the operator's own email. The kill-switch
          (tenants.outreach_blocked) protects real prospects from being
          contacted; this surface gives the operator a way to verify the
          template renders correctly without ever hitting the real lead. */}
      {!isBlacklisted && ctx.tenant.outreach_blocked && (
        <SendTestOutreachForm
          leadId={lead.id}
          defaultEmail={ctx.user_email ?? null}
        />
      )}

      {/* ─── Hero: video simulazione (Solar) — oppure info campagna custom ──
          For Solar leads: fallback chain video → GIF → static after image.
          For generic_outreach leads: show a "Campagna personalizzata" info
          panel instead — no rendering, no KPI cards.
          When ALL three Solar assets are missing AND it's not generic, hide. */}
      {isGenericOutreach ? (
        <section className="flex items-start gap-4 rounded-2xl bg-surface-container-low p-5 ring-1 ring-on-surface/5 shadow-ambient">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Mail size={20} strokeWidth={1.75} aria-hidden />
          </div>
          <div className="min-w-0 space-y-1">
            <p className="text-sm font-semibold text-on-surface">
              Campagna personalizzata
            </p>
            <p className="text-xs text-on-surface-variant leading-relaxed">
              Questo lead è stato generato tramite una campagna di outreach
              personalizzata. L&apos;email inviata usa il template configurato
              per la campagna — non è previsto un preventivo fotovoltaico
              parametrico né un rendering dell&apos;impianto.
            </p>
            {dispEmail && (
              <p className="mt-2 text-xs text-on-surface-variant">
                Destinatario:{' '}
                <span className="font-medium text-on-surface">{dispEmail}</span>
              </p>
            )}
          </div>
        </section>
      ) : (lead.rendering_video_url ||
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
            <div className="flex flex-wrap items-center gap-3">
              <RegenerateRenderingButton leadId={lead.id} />
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
              {/* Diagnostic chip: visible when only the static after-image
                  made it but the operator clicked Rigenera expecting a
                  video. The reason comes from the CreativeAgent fallback
                  path (sidecar unreachable, Solar 404, ROI missing, ...).
                  Without this chip the operator has to grep Railway logs. */}
              {!lead.rendering_video_url &&
                !lead.rendering_gif_url &&
                lead.creative_skipped_reason && (
                  <span
                    className="inline-flex items-center gap-1.5 rounded-full bg-amber-50 px-3 py-1.5 text-[11px] font-semibold text-amber-900 ring-1 ring-amber-200"
                    title={lead.creative_skipped_reason}
                  >
                    <span aria-hidden>⚠</span>
                    Video non generato · {humanReadableSkipReason(lead.creative_skipped_reason)}
                  </span>
                )}
            </div>
          </div>
          {(() => {
            // Read canonical keys with legacy-alias fallback. The v3
            // ROI service writes `yearly_savings_eur` / `co2_kg_per_year`,
            // older rows used `annual_savings_eur` / `co2_saved_kg`. Both
            // sets coexist in the DB; the UI prefers canonical and falls
            // back so leads from either era render correctly.
            const roi = lead.roi_data ?? null;
            const yearlySavings =
              roi?.yearly_savings_eur ?? roi?.annual_savings_eur ?? null;
            const co2Year = roi?.co2_kg_per_year ?? roi?.co2_saved_kg ?? null;
            const netCapex = roi?.net_capex_eur ?? null;
            const savings25y = roi?.savings_25y_eur ?? null;
            return (
              <>
                <BentoGrid cols={4}>
                  <KpiChipCard
                    label="Potenza impianto"
                    value={
                      roi?.estimated_kwp != null
                        ? `${formatNumber(roi.estimated_kwp)} kWp`
                        : '—'
                    }
                    accent="primary"
                  />
                  <KpiChipCard
                    label="Risparmio annuo"
                    value={
                      yearlySavings != null
                        ? formatEurPlain(yearlySavings)
                        : '—'
                    }
                    accent="primary"
                  />
                  <KpiChipCard
                    label="Rientro investimento"
                    value={
                      roi?.payback_years != null
                        ? `${formatNumber(roi.payback_years)} anni`
                        : '—'
                    }
                    accent="tertiary"
                  />
                  <KpiChipCard
                    label="CO₂ evitata/anno"
                    value={
                      co2Year != null ? `${formatNumber(co2Year)} kg` : '—'
                    }
                    accent="neutral"
                  />
                </BentoGrid>
                {(netCapex != null || savings25y != null) && (
                  <p className="px-1 pt-2 text-[11px] text-on-surface-variant">
                    {netCapex != null && (
                      <>
                        Investimento netto{' '}
                        <span className="font-semibold text-on-surface">
                          {formatEurPlain(netCapex)}
                        </span>
                      </>
                    )}
                    {netCapex != null && savings25y != null && ' · '}
                    {savings25y != null && (
                      <>
                        Risparmio 25 anni{' '}
                        <span className="font-semibold text-on-surface">
                          {formatEurPlain(savings25y)}
                        </span>
                      </>
                    )}
                    {roi?.self_consumption_ratio != null && (
                      <>
                        {' · '}
                        Autoconsumo{' '}
                        <span className="font-semibold text-on-surface">
                          {Math.round(roi.self_consumption_ratio * 100)}%
                        </span>
                      </>
                    )}
                  </p>
                )}
              </>
            );
          })()}
          {(lead.subjects?.sede_operativa_source || v3Signal?.google_maps_url) && (
            <p className="px-1 text-[10px] uppercase tracking-widest text-on-surface-variant">
              Sede operativa ·{' '}
              {lead.subjects?.sede_operativa_source && (
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
              )}
              {v3Signal?.google_maps_url && (
                <a
                  href={v3Signal.google_maps_url}
                  target="_blank"
                  rel="noreferrer"
                  className="ml-2 inline-flex items-center gap-0.5 font-semibold text-primary hover:underline"
                >
                  Apri su Maps
                  <ArrowUpRight size={10} strokeWidth={2.5} aria-hidden />
                </a>
              )}
            </p>
          )}
        </section>
      ) : (
        // No rendering yet AND not a generic_outreach campaign — show a
        // placeholder card with the on-demand "Genera rendering" button so
        // the operator can kick off the Creative agent without waiting for
        // the daily cron.
        <section className="space-y-3 rounded-2xl bg-surface-container-low p-5 ring-1 ring-on-surface/5 shadow-ambient">
          <p className="text-sm font-semibold text-on-surface">
            Rendering non ancora generato
          </p>
          <p className="text-xs text-on-surface-variant leading-relaxed">
            La simulazione fotovoltaica per questo tetto non è ancora stata
            prodotta. Generala adesso per popolare la pagina personale del
            lead, l&apos;email di outreach e i KPI di ROI.
          </p>
          {/* When a previous run failed, surface why so the operator can
              act (config / data fix) before clicking Rigenera again. */}
          {lead.creative_skipped_reason && (
            <p
              className="inline-flex items-center gap-1.5 rounded-lg bg-amber-50 px-3 py-1.5 text-[11px] font-semibold text-amber-900 ring-1 ring-amber-200"
              title={lead.creative_skipped_reason}
            >
              <span aria-hidden>⚠</span>
              Ultimo tentativo: {humanReadableSkipReason(lead.creative_skipped_reason)}
            </p>
          )}
          <RegenerateRenderingButton leadId={lead.id} />
        </section>
      )}

      {/* ─── Anagrafica + Tetto ───────────────────────────────────────── */}
      <BentoGrid cols={2}>
        <DataCard title="Anagrafica">
          <DataRow
            label="Tipo cliente"
            value={lead.subjects?.type === 'b2b' ? 'Azienda' : lead.subjects?.type === 'b2c' ? 'Privato' : (lead.subjects?.type?.toUpperCase() ?? '—')}
          />
          <DataRow
            label="Ragione sociale"
            value={dispRagioneSociale ?? '—'}
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
              dispEmail ? (
                <a
                  href={`mailto:${dispEmail}`}
                  className="hover:underline focus:underline focus:outline-none"
                >
                  {dispEmail}
                </a>
              ) : (
                '—'
              )
            }
          />
          {/*
            Telefono — populated by L2 scraping (v3: Places/website) or
            legacy Atoka bundle. Source badge so ops can audit data quality.
            Wrapped in a <a href="tel:"> for one-tap dial on mobile.
          */}
          <DataRow
            label="Telefono"
            value={
              dispPhone ? (
                <a
                  href={`tel:${dispPhone}`}
                  className="hover:underline focus:underline focus:outline-none"
                >
                  {dispPhone}
                </a>
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
              dispAtecoCode || dispAtecoDescription ? (
                <span className="inline-flex flex-wrap items-center justify-end gap-1.5">
                  {dispAtecoCode && (
                    <span className="rounded bg-surface-container-low px-1.5 py-0.5 font-mono text-[11px]">
                      {dispAtecoCode}
                    </span>
                  )}
                  {dispAtecoDescription && (
                    <span className="text-on-surface">
                      {dispAtecoDescription}
                    </span>
                  )}
                  {/* Mark predicted-ATECO (no description, code came from
                      Haiku) so ops can audit. Subjects-sourced ATECO has
                      both code + description. */}
                  {!lead.subjects?.ateco_code && dispAtecoCode && (
                    <span
                      className="rounded-full bg-primary-container/40 px-1.5 py-0.5 text-[10px] font-medium text-on-primary-container"
                      title="ATECO predetto da Haiku — non confermato in CCIAA"
                    >
                      predetto
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
          {/* Website URL — populated by v3 L2 scraping (or Places at L1).
              Defensive type-check: getLeadV3Signal used to mis-type
              scraped_data.website (an object of contacts) as a string,
              causing .startsWith to crash the whole page. The fetcher is
              now strict but we keep this guard so a future regression
              can't bring down the SSR. */}
          {typeof v3Signal?.website_url === 'string' && v3Signal.website_url && (
            <DataRow
              label="Sito web"
              value={
                <a
                  href={
                    v3Signal.website_url.startsWith('http')
                      ? v3Signal.website_url
                      : `https://${v3Signal.website_url}`
                  }
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 text-primary hover:underline"
                >
                  {v3Signal.website_url.replace(/^https?:\/\//, '').replace(/\/$/, '')}
                  <ExternalLink size={11} strokeWidth={2.25} aria-hidden />
                </a>
              }
            />
          )}
          {/* Source badge — hides the internal place_id (debug noise),
              shows the human-readable provenance instead. */}
          {v3Signal && (
            <DataRow
              label="Sorgente"
              value={
                <span className="rounded-full bg-primary-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-on-primary-container">
                  Discovery automatica
                </span>
              }
            />
          )}
        </DataCard>

        {/* Tetto e impianto — hidden for generic_outreach (no Solar data). */}
        {!isGenericOutreach && (
          <DataCard title="Tetto e impianto">
            <DataRow
              label="Indirizzo"
              value={
                dispTettoIndirizzo ? (
                  <span className="inline-flex items-center gap-1.5">
                    <span className="font-medium text-on-surface">
                      {dispTettoIndirizzo}
                    </span>
                    <a
                      href={
                        v3Signal?.google_maps_url ??
                        (lead.subjects?.sede_operativa_lat &&
                        lead.subjects?.sede_operativa_lng
                          ? `https://www.google.com/maps/search/?api=1&query=${lead.subjects.sede_operativa_lat},${lead.subjects.sede_operativa_lng}`
                          : `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(dispTettoIndirizzo)}`)
                      }
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-0.5 text-xs font-semibold text-primary hover:underline"
                    >
                      Apri Maps
                      <ArrowUpRight size={10} strokeWidth={2.5} aria-hidden />
                    </a>
                  </span>
                ) : (
                  '—'
                )
              }
            />
            <DataRow label="Provincia" value={dispProvincia ?? '—'} />
            <DataRow
              label="Superficie tetto"
              value={
                lead.roofs?.area_sqm
                  ? `${formatNumber(lead.roofs.area_sqm)} m²`
                  : '—'
              }
            />
            <DataRow
              label="kWp installabili"
              value={
                lead.roofs?.estimated_kwp != null
                  ? `${formatNumber(lead.roofs.estimated_kwp)} kWp`
                  : '—'
              }
            />
            {/* Pannelli stimati: derivations.panel_count is the post-funnel
                authoritative count; raw_data.solar.solarPotential.maxArrayPanelsCount
                is the Solar API max. We display the former when available. */}
            <DataRow
              label="Pannelli stimati"
              value={(() => {
                const deriv = (lead.roofs?.derivations ?? null) as
                  | Record<string, unknown>
                  | null;
                const pc = deriv?.panel_count;
                return typeof pc === 'number' && pc > 0
                  ? formatNumber(pc)
                  : '—';
              })()}
            />
            <DataRow
              label="Produzione stimata"
              value={
                lead.roofs?.estimated_yearly_kwh
                  ? `${formatNumber(lead.roofs.estimated_yearly_kwh)} kWh/anno`
                  : '—'
              }
            />
            <DataRow
              label="Esposizione"
              value={lead.roofs?.exposure ?? '—'}
            />
            <DataRow
              label="Inclinazione"
              value={
                lead.roofs?.pitch_degrees != null
                  ? `${Math.round(lead.roofs.pitch_degrees)}°`
                  : '—'
              }
            />
            <DataRow
              label="Ombreggiamento"
              value={
                lead.roofs?.shading_score != null
                  ? `${Math.round((1 - lead.roofs.shading_score) * 100)}%`
                  : '—'
              }
            />
          </DataCard>
        )}
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

        {/* Portal engagement stats — live aggregation from portal_events
            so the operator sees minute-level duration accuracy on page
            refresh, not the nightly rollup snapshot. */}
        {portalStats.sessions > 0 && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <PortalStatChip
              icon="⏱️"
              label="Tempo totale"
              value={formatDuration(portalStats.total_time_sec)}
            />
            <PortalStatChip
              icon="📅"
              label={portalStats.sessions === 1 ? 'sessione' : 'sessioni'}
              value={String(portalStats.sessions)}
            />
            <PortalStatChip
              icon="📍"
              label="Scroll max"
              value={`${portalStats.deepest_scroll_pct}%`}
            />
            {portalStats.is_live_now && (
              <span
                className="inline-flex items-center gap-1.5 rounded-full bg-error-container/80 px-2.5 py-1 text-[10px] font-bold uppercase tracking-widest text-on-error-container"
                title="Eventi registrati negli ultimi 2 minuti"
              >
                <span
                  className="h-1.5 w-1.5 rounded-full bg-error"
                  style={{ boxShadow: '0 0 6px currentColor' }}
                  aria-hidden
                />
                Live ora
              </span>
            )}
          </div>
        )}

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

      {/* ─── Scrivi follow-up ────────────────────────────────────────────
          CTA prominente che apre un dialog modale con due modalità:
          template precompilati con variabili oppure generazione AI live.
          La UX a tendina precedente nascondeva una funzione critica;
          ora è una azione di prim'ordine sulla scheda lead. */}
      {!isBlacklisted && (
        <BentoCard span="full">
          <TierLock
            feature="advanced_analytics"
            tenant={ctx.tenant}
            featureLabel="Follow-up con AI"
            inline
          >
            <FollowUpDrafter
              leadId={lead.id}
              leadName={
                lead.subjects?.business_name ||
                [lead.subjects?.owner_first_name, lead.subjects?.owner_last_name]
                  .filter(Boolean)
                  .join(' ') ||
                'questo lead'
              }
              recipientEmail={lead.subjects?.decision_maker_email ?? null}
              senderEmail={ctx.tenant.followup_from_email ?? null}
              senderName={
                ctx.tenant.email_from_name ||
                ctx.tenant.business_name ||
                'SolarLead'
              }
            />
          </TierLock>
        </BentoCard>
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

      {/* Funnel-quality breakdown — building quality, AI proxy score,
          Google Maps link. User-facing label kept neutral; internal funnel
          version (v3 geocentric) intentionally hidden. */}
      {v3Signal && (
        <CollapsibleCard
          label="Qualità lead"
          title="Dettagli scoring"
          defaultOpen={false}
        >
          <V3FunnelPanel signal={v3Signal} />
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

function PortalStatChip({
  icon,
  label,
  value,
}: {
  icon: string;
  label: string;
  value: string;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-surface-container px-2.5 py-1 ring-1 ring-on-surface/5">
      <span aria-hidden>{icon}</span>
      <span className="font-semibold tabular-nums text-on-surface">{value}</span>
      <span className="text-on-surface-variant">{label}</span>
    </span>
  );
}

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
  // Auto-hide: when the caller passes a "missing" sentinel we omit the row
  // entirely instead of rendering a wall of "—" placeholders. Coverage:
  //   - null / undefined          (most common)
  //   - empty string ''            (truthy-coerced empty value)
  //   - the literal '—'            (call sites do `value={x ?? '—'}`)
  //   - array []                   (predicted_ateco_codes when empty)
  // ReactNode wrappers (e.g. <span>...</span>) are always rendered — call
  // sites already guard those with `cond ? <node/> : '—'`.
  if (
    value == null ||
    value === '' ||
    value === '—' ||
    (Array.isArray(value) && value.length === 0)
  ) {
    return null;
  }
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
// humanReadableSkipReason — translate creative_skipped_reason into Italian
//
// CreativeAgent writes raw enum-style strings to leads.creative_skipped_reason
// (`remotion_failed`, `before_url_missing`, `roof_confidence_too_low:mapbox_hq`,
// `ai_paint_error:Replicate timed out`). Operators don't read English enums —
// translate to short, actionable Italian. Anything we don't recognise falls
// through to the raw string so the chip never renders empty.
// ---------------------------------------------------------------------------
function humanReadableSkipReason(reason: string): string {
  // Strip the trailing `:detail` so colon-prefixed reasons match the base key.
  const base = reason.split(':')[0] ?? reason;
  const MAP: Record<string, string> = {
    remotion_failed: 'sidecar non raggiungibile (controlla VIDEO_RENDERER_URL)',
    before_url_missing: 'Solar API non ha trovato il tetto',
    after_url_missing: 'AI panel-paint fallito',
    roi_missing: 'numeri ROI non disponibili',
    missing_coords: 'coordinate del tetto mancanti',
    solar_api_key_not_configured: 'GOOGLE_SOLAR_API_KEY non configurata',
    replicate_token_not_configured: 'REPLICATE_API_TOKEN non configurato',
    roof_confidence_too_low: 'sede non confermata — apri il picker',
    solar_no_building: 'Solar API non riconosce un edificio in queste coordinate',
    ai_paint_error: 'errore generazione AI panel-paint',
    solar_render_error: 'errore Solar API durante il render',
  };
  return MAP[base] ?? reason;
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
// V3FunnelPanel — Sprint 8
//
// Shows the v3-specific enrichment signals for a geocentric lead:
//   * Building quality score bar (L3 heuristics, 0-5)
//   * Proxy score breakdown (L5 Haiku scores)
//   * Google Maps link
//   * google_place_id truncated chip
// ---------------------------------------------------------------------------

const PROXY_SCORE_LABELS: Record<string, string> = {
  icp_fit_score: 'ICP fit',
  building_quality_score: 'Qualità edificio',
  solar_potential_score: 'Potenziale solare',
  contact_completeness_score: 'Completezza contatti',
  overall_score: 'Punteggio totale',
};

function V3FunnelPanel({ signal }: { signal: LeadV3Signal }) {
  const proxyEntries = signal.proxy_score_data
    ? (Object.entries(signal.proxy_score_data) as [string, unknown][])
        .filter(([k, v]) => typeof v === 'number' && k !== 'overall_score')
        .sort(([, a], [, b]) => (b as number) - (a as number))
    : [];

  return (
    <div className="space-y-5 pt-1">
      {/* Header row: Google Maps link + place_id chip */}
      <div className="flex flex-wrap items-center gap-3">
        {signal.google_maps_url && (
          <a
            href={signal.google_maps_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1.5 rounded-full bg-primary/10 px-3 py-1.5 text-xs font-semibold text-primary transition-opacity hover:opacity-80"
          >
            <ArrowUpRight size={12} strokeWidth={2.5} aria-hidden />
            Apri su Google Maps
          </a>
        )}
        {signal.google_place_id && (
          <span
            className="rounded bg-surface-container-low px-2 py-1 font-mono text-[10px] text-on-surface-variant"
            title={`Google Place ID: ${signal.google_place_id}`}
          >
            {signal.google_place_id}
          </span>
        )}
      </div>

      {/* Building quality score — L3 heuristics 0-5 */}
      {signal.building_quality_score != null && (
        <div>
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Qualità edificio (L3 euristiche)
          </p>
          <div className="flex items-center gap-3">
            <div className="flex gap-1">
              {Array.from({ length: 5 }).map((_, i) => (
                <span
                  key={i}
                  className={cn(
                    'h-3 w-3 rounded-full',
                    i < (signal.building_quality_score ?? 0)
                      ? 'bg-primary'
                      : 'bg-surface-container-low',
                  )}
                  aria-hidden
                />
              ))}
            </div>
            <span className="font-headline text-sm font-bold tabular-nums text-on-surface">
              {signal.building_quality_score}/5
            </span>
          </div>
          <p className="mt-1 text-[11px] text-on-surface-variant">
            Basato su: rating Google · sito web rilevato · telefono trovato ·
            business_status OPERATIONAL
          </p>
        </div>
      )}

      {/* Proxy score breakdown — L5 Haiku */}
      {proxyEntries.length > 0 && (
        <div>
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Proxy score L5 (Haiku)
          </p>
          <div className="space-y-2">
            {proxyEntries.map(([key, raw]) => {
              const value = Math.round(raw as number);
              const pct = Math.max(0, Math.min(100, value));
              return (
                <div key={key} className="flex items-center gap-3">
                  <span className="w-40 shrink-0 text-xs text-on-surface-variant">
                    {PROXY_SCORE_LABELS[key] ?? key}
                  </span>
                  <div className="h-2 flex-1 rounded-full bg-surface-container-low">
                    <div
                      className="h-full rounded-full bg-primary"
                      style={{ width: `${pct}%` }}
                      aria-hidden
                    />
                  </div>
                  <span className="w-8 text-right font-mono text-xs font-semibold text-on-surface">
                    {value}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Haiku reasoning snippet (if present) */}
      {signal.proxy_score_data?.reasoning && (
        <div className="rounded-xl bg-surface-container-low px-4 py-3">
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Motivazione Haiku
          </p>
          <p className="text-xs leading-relaxed text-on-surface-variant">
            {signal.proxy_score_data.reasoning}
          </p>
        </div>
      )}

      {/* Size category */}
      {signal.proxy_score_data?.predicted_size_category && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-on-surface-variant">Dimensione stimata:</span>
          <span className="rounded-full bg-surface-container-low px-2.5 py-0.5 text-xs font-semibold text-on-surface capitalize">
            {signal.proxy_score_data.predicted_size_category}
          </span>
        </div>
      )}

      <p className="text-[11px] text-on-surface-variant">
        Dati generati da FLUSSO 1 v3 geocentrico (L1 Google Places → L2
        scraping → L3 qualità → L4 Solar API → L5 Haiku). Nessun utilizzo di
        Atoka o BIC.
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
