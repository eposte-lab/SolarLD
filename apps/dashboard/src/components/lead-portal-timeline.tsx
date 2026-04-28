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
  Hand,
  LineChart,
  Mail,
  MessageCircle,
  MousePointerClick,
  PlayCircle,
  ScrollText,
  Volume2,
  Maximize,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import type { PortalEventRow } from '@/lib/data/engagement';
import { relativeTime } from '@/lib/utils';

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
  'portal.cta_hover': {
    icon: Hand,
    label: 'Hover su una CTA',
    tone: 'bg-surface-container-high text-on-surface-variant',
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
  'portal.appointment_click': {
    icon: CalendarCheck,
    label: 'Richiesta sopralluogo',
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

  // Group consecutive heartbeats so the timeline doesn't drown in
  // 15-second keepalives. We collapse runs of 3+ heartbeats into a
  // single "X heartbeats" row.
  type Item = { kind: 'event'; row: PortalEventRow } | {
    kind: 'collapsed';
    count: number;
    firstAt: string;
    lastAt: string;
  };
  const items: Item[] = [];
  let buffer: PortalEventRow[] = [];
  const flushBuffer = () => {
    if (buffer.length === 0) return;
    const oldest = buffer[buffer.length - 1];
    const newest = buffer[0];
    if (buffer.length >= 3 && oldest && newest) {
      items.push({
        kind: 'collapsed',
        count: buffer.length,
        firstAt: oldest.occurred_at,
        lastAt: newest.occurred_at,
      });
    } else {
      for (const r of buffer) items.push({ kind: 'event', row: r });
    }
    buffer = [];
  };
  for (const r of events) {
    if (r.event_kind === 'portal.heartbeat') {
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
        if (item.kind === 'collapsed') {
          return (
            <li
              key={`hb-${idx}`}
              className="flex items-center gap-3 rounded-md bg-surface-container-low px-4 py-2 text-xs text-on-surface-variant"
            >
              <Activity size={14} aria-hidden />
              <span className="flex-1">
                {item.count} heartbeat in sessione
              </span>
              <span className="opacity-70">
                {relativeTime(item.firstAt)} \u2192 {relativeTime(item.lastAt)}
              </span>
            </li>
          );
        }
        const row = item.row;
        const style = EVENT_STYLES[row.event_kind] ?? FALLBACK_STYLE;
        const Icon = style.icon;
        const detail = formatEventDetail(row);
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
