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
  /** True when the step has happened (node lit, connector filled). */
  active: boolean;
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
    },
    {
      key: 'clicked',
      label: 'Cliccata',
      Icon: MousePointerClick,
      at: flags.outreachClickedAt,
      active: flags.outreachClickedAt != null,
    },
    {
      key: 'portal',
      label: 'Portale',
      Icon: Globe,
      at: flags.portalVisitedAt,
      active: flags.portalVisitedAt != null,
    },
    {
      key: 'bolletta',
      label: 'Bolletta',
      Icon: FileText,
      at: flags.bollettaUploadedAt,
      active: flags.bollettaUploadedAt != null,
    },
    {
      key: 'appointment',
      label: 'Appuntamento',
      Icon: CalendarCheck,
      at: flags.appointmentRequestedAt,
      active: flags.appointmentRequestedAt != null,
    },
  ];

  const lastIdx = pills.length - 1;

  return (
    <div className={`flex items-start ${className ?? ''}`}>
      {pills.map((p, idx) => {
        const done = p.active;
        const isFirst = idx === 0;
        const isLast = idx === lastIdx;
        // Un connettore è "pieno" quando lo step verso cui porta è fatto.
        const leftFilled = !isFirst && done;
        const rightFilled = !isLast && (pills[idx + 1]?.active ?? false);
        const conn = (filled: boolean): string =>
          `h-0.5 flex-1 ${filled ? 'bg-primary' : 'bg-on-surface/12'}`;
        return (
          <div key={p.key} className="flex flex-1 flex-col items-center">
            <div className="flex w-full items-center">
              {/* Il primo/ultimo step non ha connettore esterno: niente
                  flex-1, così il nodo 1 parte a sinistra e il 6 finisce
                  a destra invece di restare centrati nella loro cella. */}
              {isFirst ? (
                <span className="flex-none" />
              ) : (
                <span className={conn(leftFilled)} aria-hidden />
              )}
              <span
                title={
                  done
                    ? `${p.label} · ${relativeTime(p.at)}`
                    : `${p.label} · non ancora`
                }
                className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-full ${
                  done
                    ? 'bg-primary text-on-primary'
                    : 'border-2 border-dashed border-on-surface/25 text-on-surface-variant/50'
                }`}
              >
                <p.Icon size={14} strokeWidth={2.5} aria-hidden />
              </span>
              {isLast ? (
                <span className="flex-none" />
              ) : (
                <span className={conn(rightFilled)} aria-hidden />
              )}
            </div>
            <span
              className={`mt-1.5 text-center text-[11px] font-semibold ${
                done ? 'text-on-surface' : 'text-on-surface-variant/60'
              }`}
            >
              {p.label}
            </span>
            <span
              className={`text-center text-[10px] ${
                done ? 'text-on-surface-variant' : 'text-on-surface-variant/40'
              }`}
            >
              {done ? relativeTime(p.at) : 'in attesa'}
            </span>
          </div>
        );
      })}
    </div>
  );
}
