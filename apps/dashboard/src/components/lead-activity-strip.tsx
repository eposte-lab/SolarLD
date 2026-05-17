/**
 * LeadActivityStrip — the lead's activity funnel as a horizontal stepper.
 *
 * The operator opens the lead and wants to know in 2 seconds how far the
 * lead got: inviata → letta → cliccata → portale → bolletta → appuntamento.
 * Reached steps are lit and connected by a filled line; future steps are
 * dimmed. Tooltip carries the timestamp. No counts here — the timeline
 * below has the full chronology; this stepper is the at-a-glance funnel.
 */

import {
  CalendarCheck,
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

  const nodeStyle = (p: PillSpec): string => {
    if (!p.active) return 'bg-surface-container text-on-surface-variant';
    if (p.accent === 'conversion') {
      return 'bg-tertiary-container text-on-tertiary-container';
    }
    if (p.accent === 'engagement') {
      return 'bg-secondary-container text-on-secondary-container';
    }
    return 'bg-primary-container text-on-primary-container';
  };

  return (
    <div className={`flex items-start ${className ?? ''}`}>
      {pills.map((p, idx) => {
        const tooltip = p.active
          ? `${p.label} · ${relativeTime(p.at)}`
          : `${p.label} · non ancora`;
        // A connector segment is "filled" when the step it leads INTO has
        // been reached. Drawn on both sides so adjacent steps meet.
        const leftFilled = idx > 0 && p.active;
        const rightFilled =
          idx < pills.length - 1 && (pills[idx + 1]?.active ?? false);
        const seg = (filled: boolean, hidden: boolean): string =>
          `h-0.5 flex-1 ${hidden ? 'opacity-0' : filled ? 'bg-primary/50' : 'bg-on-surface/12'}`;
        return (
          <div key={p.key} className="flex flex-1 flex-col items-center">
            <div className="flex w-full items-center">
              <span className={seg(leftFilled, idx === 0)} aria-hidden />
              <span
                title={tooltip}
                className={`inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${nodeStyle(p)}`}
              >
                <p.Icon size={13} strokeWidth={2.5} aria-hidden />
              </span>
              <span
                className={seg(rightFilled, idx === pills.length - 1)}
                aria-hidden
              />
            </div>
            <span
              className={`mt-1 text-center text-[10px] font-semibold ${
                p.active ? 'text-on-surface' : 'text-on-surface-variant'
              }`}
            >
              {p.label}
            </span>
          </div>
        );
      })}
    </div>
  );
}
