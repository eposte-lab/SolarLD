/**
 * LeadPortalTimeline — "Attività portale" section on the lead detail
 * page. Renders the ``portal_events`` rows for a single lead in
 * reverse-chronological order, each row decorated with a meaningful
 * icon + Italian label so the operator can read "what happened" at a
 * glance.
 *
 * Companion to the existing email/CRM ``Timeline eventi`` block — that
 * one shows outreach lifecycle (sent / delivered / opened / clicked),
 * this one shows what the *recipient* did once they opened the dossier
 * (scrolled, watched the video, uploaded the bill, clicked WhatsApp,
 * …). Both are needed to triage a hot lead — the operator wants both
 * "did the email work" and "did they engage".
 *
 * Pure server component — receives pre-fetched rows from the page.
 */

import {
  Activity,
  CalendarCheck,
  CheckCircle2,
  CircleDot,
  FileImage,
  FilePen,
  Inbox,
  LineChart,
  Mail,
  MessageCircle,
  MousePointerClick,
  PenLine,
  PlayCircle,
  ScrollText,
  Volume2,
  Maximize,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import type { PortalEventRow } from '@/lib/data/engagement';
import { relativeTime } from '@/lib/utils';

import { BollettaTimelineRow } from './bolletta-timeline-row';

type EventStyle = {
  icon: LucideIcon;
  label: string;
  /** Tailwind background utility for the icon chip. */
  tone: string;
};

const EVENT_STYLES: Record<string, EventStyle> = {
  'portal.view': {
    icon: CircleDot,
    label: 'Apertura portale',
    tone: 'bg-surface-container-high text-on-surface-variant',
  },
  'portal.heartbeat': {
    icon: Activity,
    label: 'In sessione',
    tone: 'bg-surface-container-high text-on-surface-variant',
  },
  'portal.leave': {
    icon: CircleDot,
    label: 'Uscita',
    tone: 'bg-surface-container-high text-on-surface-variant opacity-70',
  },
  'portal.scroll_50': {
    icon: ScrollText,
    label: 'Letto fino a metà',
    tone: 'bg-surface-container-high text-on-surface',
  },
  'portal.scroll_90': {
    icon: ScrollText,
    label: 'Letto fino in fondo',
    tone: 'bg-tertiary-container text-on-tertiary-container',
  },
  'portal.roi_viewed': {
    icon: LineChart,
    label: 'Ha visto le stime ROI',
    tone: 'bg-tertiary-container text-on-tertiary-container',
  },
  'portal.video_play': {
    icon: PlayCircle,
    label: 'Ha avviato il video',
    tone: 'bg-tertiary-container text-on-tertiary-container',
  },
  'portal.video_complete': {
    icon: CheckCircle2,
    label: 'Ha guardato tutto il video',
    tone: 'bg-secondary-container text-on-secondary-container',
  },
  'portal.audio_on': {
    icon: Volume2,
    label: 'Ha attivato l\u2019audio',
    tone: 'bg-tertiary-container text-on-tertiary-container',
  },
  'portal.video_fullscreen': {
    icon: Maximize,
    label: 'Video a tutto schermo',
    tone: 'bg-tertiary-container text-on-tertiary-container',
  },
  'portal.whatsapp_click': {
    icon: MessageCircle,
    label: 'Click su WhatsApp',
    tone: 'bg-secondary-container text-on-secondary-container',
  },
  'portal.contact_view': {
    icon: Inbox,
    label: 'Ha aperto il form di contatto',
    tone: 'bg-tertiary-container text-on-tertiary-container',
  },
  'portal.contact_started': {
    icon: PenLine,
    label: 'Ha iniziato a compilare il form',
    tone: 'bg-tertiary-container text-on-tertiary-container',
  },
  'portal.contact_abandoned': {
    icon: FilePen,
    label: 'Ha compilato il form senza inviarlo',
    tone: 'bg-secondary-container text-on-secondary-container',
  },
  'portal.appointment_click': {
    icon: CalendarCheck,
    label: 'Ha cliccato «Contattaci subito»',
    tone: 'bg-secondary-container text-on-secondary-container',
  },
  'portal.email_reply_click': {
    icon: Mail,
    label: 'Click su rispondi via email',
    tone: 'bg-secondary-container text-on-secondary-container',
  },
  'portal.bolletta_uploaded': {
    icon: FileImage,
    label: 'Ha caricato la bolletta',
    tone: 'bg-secondary-container text-on-secondary-container',
  },
};

const FALLBACK_STYLE: EventStyle = {
  icon: MousePointerClick,
  label: 'Evento sul portale',
  tone: 'bg-surface-container-high text-on-surface-variant',
};

function formatEventDetail(row: PortalEventRow): string | null {
  const md = row.metadata;
  if (!md || typeof md !== 'object') return null;
  // Abandoned contact form — surface the partial data the lead typed +
  // whether they had ticked GDPR consent at the moment they left.
  if (row.event_kind === 'portal.contact_abandoned') {
    const m = md as Record<string, unknown>;
    const str = (k: string) => (typeof m[k] === 'string' ? (m[k] as string).trim() : '');
    const parts: string[] = [];
    for (const k of ['contact_name', 'phone', 'email', 'preferred_time', 'notes']) {
      const v = str(k);
      if (v) parts.push(v);
    }
    parts.push(m.gdpr_consent === true ? 'consenso ✓' : 'consenso ✗');
    return parts.join(' · ');
  }
  // A few well-known metadata fields we surface inline.
  if (row.event_kind === 'portal.bolletta_uploaded') {
    const kwh = (md as { kwh?: number }).kwh;
    const eur = (md as { eur?: number }).eur;
    if (kwh && eur) {
      return `${Math.round(kwh).toLocaleString('it-IT')} kWh / ${Math.round(eur).toLocaleString('it-IT')} \u20AC stimati`;
    }
  }
  if (row.event_kind === 'portal.scroll_50' || row.event_kind === 'portal.scroll_90') {
    const pct = (md as { pct?: number }).pct;
    if (typeof pct === 'number') return `${pct}% scroll`;
  }
  if (row.event_kind === 'portal.video_play' || row.event_kind === 'portal.video_complete') {
    const dur = (md as { duration?: number }).duration;
    if (typeof dur === 'number') return `${Math.round(dur)}s`;
  }
  return null;
}

// Eventi "passivi" — apertura, lettura, permanenza. Collassati in una
// riga di riepilogo per non intasare la timeline. Tutto il resto
// (video, CTA di contatto, bolletta) è un evento di intenzione e ha
// una riga propria. `portal.cta_hover` resta qui per i dati storici:
// l'hover non viene più tracciato ma vecchie righe possono esistere.
const PASSIVE_KINDS = new Set<string>([
  'portal.view',
  'portal.heartbeat',
  'portal.leave',
  'portal.scroll_50',
  'portal.scroll_90',
  'portal.roi_viewed',
  'portal.cta_hover',
]);

type TimelineItem =
  | { kind: 'event'; row: PortalEventRow }
  | { kind: 'summary'; events: PortalEventRow[] };

type Summary = {
  icon: LucideIcon;
  label: string;
  tone: string;
  detail: string;
  at: string;
};

/** Collassa un gruppo di eventi passivi in un'unica riga di riepilogo. */
function buildSummary(events: PortalEventRow[]): Summary {
  const kinds = new Set(events.map((e) => e.event_kind));
  const heartbeats = events.filter((e) => e.event_kind === 'portal.heartbeat').length;

  let label: string;
  let icon: LucideIcon = CircleDot;
  let tone = 'bg-surface-container-high text-on-surface-variant';
  if (kinds.has('portal.scroll_90')) {
    label = 'Ha letto il preventivo fino in fondo';
    icon = ScrollText;
    tone = 'bg-tertiary-container text-on-tertiary-container';
  } else if (kinds.has('portal.scroll_50')) {
    label = 'Ha letto metà del preventivo';
    icon = ScrollText;
  } else if (kinds.has('portal.view')) {
    label = 'Ha aperto il portale';
  } else {
    label = 'In sessione sul portale';
  }

  const parts: string[] = [];
  if (kinds.has('portal.roi_viewed')) parts.push('ha visto le stime ROI');
  if (heartbeats > 0) {
    const sec = heartbeats * 15;
    parts.push(
      sec >= 60 ? `~${Math.round(sec / 60)} min sul portale` : `~${sec}s sul portale`,
    );
  }

  return {
    icon,
    label,
    tone,
    detail: parts.join(' · '),
    // Gli eventi arrivano dal più recente — events[0] è il più nuovo.
    at: events[0]?.occurred_at ?? '',
  };
}

export function LeadPortalTimeline({ events }: { events: PortalEventRow[] }) {
  if (events.length === 0) {
    return (
      <div className="rounded-lg bg-surface-container-low px-5 py-6 text-center">
        <p className="text-sm text-on-surface-variant">
          Nessuna attività registrata sul portale per questo lead. Quando il
          destinatario aprirà il proprio dossier, gli eventi compariranno qui.
        </p>
      </div>
    );
  }

  // Gerarchia: gli eventi "passivi" (apertura, scroll, heartbeat,
  // visita ROI, uscita) sono rumore se mostrati uno per uno \u2014 una
  // visita di 15s ne genera 7-8. Li collassiamo in UNA riga di
  // riepilogo ("Ha letto il preventivo \u00b7 ~2 min"); gli eventi ad alta
  // intenzione (video, WhatsApp, appuntamento, bolletta) restano righe
  // distinte e prominenti.
  const items: TimelineItem[] = [];
  let buffer: PortalEventRow[] = [];
  const flushBuffer = () => {
    if (buffer.length === 0) return;
    if (buffer.length >= 2) {
      items.push({ kind: 'summary', events: buffer });
    } else {
      for (const r of buffer) items.push({ kind: 'event', row: r });
    }
    buffer = [];
  };
  for (const r of events) {
    if (PASSIVE_KINDS.has(r.event_kind)) {
      buffer.push(r);
    } else {
      flushBuffer();
      items.push({ kind: 'event', row: r });
    }
  }
  flushBuffer();

  return (
    <ol className="space-y-2">
      {items.map((item, idx) => {
        if (item.kind === 'summary') {
          const s = buildSummary(item.events);
          const Icon = s.icon;
          return (
            <li
              key={`sum-${idx}`}
              className="flex items-center gap-3 rounded-md bg-surface-container-low px-4 py-2.5"
            >
              <span
                className={`inline-flex h-7 w-7 items-center justify-center rounded-full ${s.tone}`}
              >
                <Icon size={14} aria-hidden />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-on-surface">
                  {s.label}
                </p>
                {s.detail && (
                  <p className="truncate text-xs text-on-surface-variant">
                    {s.detail}
                  </p>
                )}
              </div>
              <span className="shrink-0 text-xs text-on-surface-variant">
                {relativeTime(s.at)}
              </span>
            </li>
          );
        }
        const row = item.row;
        const style = EVENT_STYLES[row.event_kind] ?? FALLBACK_STYLE;
        const Icon = style.icon;
        const detail = formatEventDetail(row);
        // Bolletta caricata → riga "premium" cliccabile (aura mint +
        // shimmer) che scrolla alla BollettaCard. È il segnale ad alta
        // intenzione del funnel, merita risalto.
        if (row.event_kind === 'portal.bolletta_uploaded') {
          return (
            <BollettaTimelineRow
              key={row.id}
              label={style.label}
              detail={detail}
              at={relativeTime(row.occurred_at)}
            />
          );
        }
        return (
          <li
            key={row.id}
            className="flex items-center gap-3 rounded-md bg-surface-container-low px-4 py-2.5"
          >
            <span
              className={`inline-flex h-7 w-7 items-center justify-center rounded-full ${style.tone}`}
            >
              <Icon size={14} aria-hidden />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-on-surface">
                {style.label}
              </p>
              {detail && (
                <p className="truncate text-xs text-on-surface-variant">
                  {detail}
                </p>
              )}
            </div>
            <span className="shrink-0 text-xs text-on-surface-variant">
              {relativeTime(row.occurred_at)}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
