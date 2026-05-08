/**
 * LeadActivityStrip — at-a-glance status pills for the lead detail header.
 *
 * The operator opens the lead and wants to know in 2 seconds:
 *   - did we send the email?
 *   - did they read it?
 *   - did they click?
 *   - did they visit the portal?
 *   - did they upload a bolletta?
 *   - is there an appointment?
 *
 * Each step renders as a coloured pill when the event happened, dimmed
 * when not. Tooltip carries the timestamp. No counts here — the timeline
 * below has the full chronology; this strip is for state-at-a-glance.
 */

import {
  CalendarCheck,
  Eye,
  FileText,
  Globe,
  MailCheck,
  MousePointerClick,
  Send,
} from 'lucide-react';

import { relativeTime } from '@/lib/utils';

export interface LeadActivityFlags {
  outreachSentAt: string | null;
  outreachOpenedAt: string | null;
  outreachClickedAt: string | null;
  portalVisitedAt: string | null;
  bollettaUploadedAt: string | null;
  appointmentRequestedAt: string | null;
}

interface PillSpec {
  key: string;
  label: string;
  Icon: typeof Send;
  at: string | null;
  /** When false the pill is dimmed (not yet happened). */
  active: boolean;
  /** Highlight the conversion step. */
  accent?: 'engagement' | 'conversion';
}

export function LeadActivityStrip({
  flags,
  className,
}: {
  flags: LeadActivityFlags;
  className?: string;
}) {
  const pills: PillSpec[] = [
    {
      key: 'sent',
      label: 'Inviata',
      Icon: Send,
      at: flags.outreachSentAt,
      active: flags.outreachSentAt != null,
    },
    {
      key: 'opened',
      label: 'Letta',
      Icon: MailCheck,
      at: flags.outreachOpenedAt,
      active: flags.outreachOpenedAt != null,
      accent: 'engagement',
    },
    {
      key: 'clicked',
      label: 'Cliccata',
      Icon: MousePointerClick,
      at: flags.outreachClickedAt,
      active: flags.outreachClickedAt != null,
      accent: 'engagement',
    },
    {
      key: 'portal',
      label: 'Portale',
      Icon: Globe,
      at: flags.portalVisitedAt,
      active: flags.portalVisitedAt != null,
      accent: 'engagement',
    },
    {
      key: 'bolletta',
      label: 'Bolletta',
      Icon: FileText,
      at: flags.bollettaUploadedAt,
      active: flags.bollettaUploadedAt != null,
      accent: 'engagement',
    },
    {
      key: 'appointment',
      label: 'Appuntamento',
      Icon: CalendarCheck,
      at: flags.appointmentRequestedAt,
      active: flags.appointmentRequestedAt != null,
      accent: 'conversion',
    },
  ];

  return (
    <div className={`flex flex-wrap items-center gap-1.5 ${className ?? ''}`}>
      {pills.map((p) => {
        const tooltip = p.active
          ? `${p.label} · ${relativeTime(p.at)}`
          : `${p.label} · non ancora`;
        const styles = p.active
          ? p.accent === 'conversion'
            ? 'bg-tertiary-container text-on-tertiary-container'
            : p.accent === 'engagement'
              ? 'bg-secondary-container text-on-secondary-container'
              : 'bg-primary-container text-on-primary-container'
          : 'bg-surface-container text-on-surface-variant opacity-50';
        return (
          <span
            key={p.key}
            title={tooltip}
            className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-[11px] font-semibold ${styles}`}
          >
            <p.Icon size={12} strokeWidth={2.5} aria-hidden />
            <span>{p.label}</span>
            {p.active && p.key === 'opened' && (
              <Eye size={10} strokeWidth={2.5} aria-hidden className="ml-0.5 opacity-70" />
            )}
          </span>
        );
      })}
    </div>
  );
}
