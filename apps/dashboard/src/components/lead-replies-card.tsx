'use client';

/**
 * LeadRepliesCard — displays inbound email replies from a lead with
 * Claude-analysed sentiment / intent badges and a copyable suggested reply.
 *
 * Part B.2. Data is SSR-fetched by the server page and passed as props;
 * this client component handles copy-to-clipboard interactivity only.
 */

import { useState } from 'react';

import { cn, relativeTime } from '@/lib/utils';
import type { LeadReplyRow, ReplyIntent, ReplySentiment, ReplyUrgency } from '@/types/db';

interface Props {
  replies: LeadReplyRow[];
}

export function LeadRepliesCard({ replies }: Props) {
  if (replies.length === 0) {
    return (
      <div className="rounded-lg bg-surface-container-low px-6 py-10 text-center">
        <p className="text-sm text-on-surface-variant">
          Nessuna risposta ricevuta finora. Quando un lead risponde alla tua
          email di outreach, la risposta comparirà qui con analisi AI di
          sentiment e intento.
        </p>
        <p className="mt-2 text-xs text-on-surface-variant/60">
          Assicurati di configurare il webhook inbound Resend e di impostare
          la variabile <code className="font-mono">RESEND_INBOUND_SECRET</code>.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {replies.map((reply) => (
        <ReplyRow key={reply.id} reply={reply} />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------

function ReplyRow({ reply }: { reply: LeadReplyRow }) {
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const bodyPreview =
    (reply.body_text ?? '').length > 200 && !expanded
      ? reply.body_text!.slice(0, 200) + '…'
      : reply.body_text ?? '';

  async function copyReply() {
    if (!reply.suggested_reply) return;
    try {
      await navigator.clipboard.writeText(reply.suggested_reply);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="rounded-xl border border-outline-variant/30 bg-surface-container-lowest p-4">
      {/* Header row */}
      <div className="flex flex-wrap items-start gap-2">
        <div className="flex-1 min-w-0">
          <p className="truncate text-sm font-semibold text-on-surface">
            {reply.from_email}
          </p>
          {reply.reply_subject && (
            <p className="mt-0.5 truncate text-xs text-on-surface-variant">
              {reply.reply_subject}
            </p>
          )}
        </div>
        <span className="shrink-0 text-xs text-on-surface-variant">
          {relativeTime(reply.received_at)}
        </span>
      </div>

      {/* Analysis badges */}
      {reply.analyzed_at ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {reply.sentiment && (
            <SentimentBadge sentiment={reply.sentiment} />
          )}
          {reply.intent && (
            <IntentBadge intent={reply.intent} />
          )}
          {reply.urgency && (
            <UrgencyBadge urgency={reply.urgency} />
          )}
        </div>
      ) : reply.analysis_error ? (
        <p className="mt-2 text-xs text-on-surface-variant/60">
          Analisi AI non disponibile: {reply.analysis_error}
        </p>
      ) : (
        <p className="mt-2 text-xs text-on-surface-variant/60 italic">
          Analisi AI in corso…
        </p>
      )}

      {/* Body preview */}
      {reply.body_text && (
        <div className="mt-3">
          <p className="whitespace-pre-wrap text-xs text-on-surface-variant leading-relaxed">
            {bodyPreview}
          </p>
          {(reply.body_text ?? '').length > 200 && (
            <button
              onClick={() => setExpanded((p) => !p)}
              className="mt-1 text-xs font-semibold text-primary hover:underline"
            >
              {expanded ? 'Mostra meno' : 'Mostra tutto'}
            </button>
          )}
        </div>
      )}

      {/* Suggested reply */}
      {reply.suggested_reply && (
        <div className="mt-4 rounded-lg bg-surface-container p-3">
          <div className="mb-1 flex items-center justify-between gap-2">
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Risposta suggerita da AI
            </p>
            <button
              onClick={copyReply}
              className={cn(
                'shrink-0 rounded-md px-2.5 py-1 text-xs font-semibold transition-colors',
                copied
                  ? 'bg-primary-container text-on-primary-container'
                  : 'bg-surface-container-high text-on-surface hover:bg-surface-container-highest',
              )}
            >
              {copied ? '✓ Copiato' : 'Copia'}
            </button>
          </div>
          <p className="whitespace-pre-wrap text-xs text-on-surface leading-relaxed">
            {reply.suggested_reply}
          </p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Badge sub-components
// ---------------------------------------------------------------------------

const SENTIMENT_MAP: Record<ReplySentiment, { label: string; cls: string }> = {
  positive: {
    label: 'Positivo',
    cls: 'bg-primary-container/60 text-on-primary-container',
  },
  neutral: {
    label: 'Neutro',
    cls: 'bg-surface-container-high text-on-surface-variant',
  },
  negative: {
    label: 'Negativo',
    cls: 'bg-error-container/50 text-on-error-container',
  },
  unclear: {
    label: 'Non chiaro',
    cls: 'bg-surface-container-high text-on-surface-variant',
  },
};

const INTENT_MAP: Record<ReplyIntent, { label: string; cls: string }> = {
  interested: {
    label: 'Interessato',
    cls: 'bg-primary-container/40 text-on-primary-container',
  },
  question: {
    label: 'Domanda',
    cls: 'bg-secondary-container/50 text-on-secondary-container',
  },
  objection: {
    label: 'Obiezione',
    cls: 'bg-error-container/40 text-on-error-container',
  },
  appointment_request: {
    label: 'Richiede appuntamento',
    cls: 'bg-tertiary-container/60 text-on-tertiary-container',
  },
  unsubscribe: {
    label: 'Disiscrizione',
    cls: 'bg-error-container/30 text-on-error-container',
  },
  other: {
    label: 'Altro',
    cls: 'bg-surface-container-high text-on-surface-variant',
  },
};

const URGENCY_MAP: Record<ReplyUrgency, { label: string; cls: string }> = {
  high: {
    label: '⚡ Urgente',
    cls: 'bg-error/15 text-error font-semibold',
  },
  medium: {
    label: 'Media priorità',
    cls: 'bg-secondary-container/40 text-on-secondary-container',
  },
  low: {
    label: 'Bassa priorità',
    cls: 'bg-surface-container-high text-on-surface-variant',
  },
};

function SentimentBadge({ sentiment }: { sentiment: ReplySentiment }) {
  const { label, cls } = SENTIMENT_MAP[sentiment] ?? SENTIMENT_MAP.unclear;
  return <Badge label={label} extraCls={cls} />;
}

function IntentBadge({ intent }: { intent: ReplyIntent }) {
  const { label, cls } = INTENT_MAP[intent] ?? INTENT_MAP.other;
  return <Badge label={label} extraCls={cls} />;
}

function UrgencyBadge({ urgency }: { urgency: ReplyUrgency }) {
  const { label, cls } = URGENCY_MAP[urgency] ?? URGENCY_MAP.low;
  return <Badge label={label} extraCls={cls} />;
}

function Badge({ label, extraCls }: { label: string; extraCls: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px]',
        extraCls,
      )}
    >
      {label}
    </span>
  );
}
