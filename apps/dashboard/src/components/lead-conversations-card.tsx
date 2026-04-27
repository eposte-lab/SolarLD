'use client';

/**
 * LeadConversationsCard — WhatsApp conversation thread viewer.
 *
 * Displays the full bidirectional thread in a WhatsApp-style UI:
 *  - Lead messages: left-aligned bubble (gray)
 *  - AI replies: right-aligned bubble (primary tint)
 *  - System/handoff messages: center, smaller, muted
 *
 * State badges:
 *  - active  → 🤖 Auto (AI is replying)
 *  - handoff → 👤 Operatore (human takes over)
 *  - closed  → ✓ Chiusa
 *
 * Real-time updates: subscribes to Supabase Realtime on the
 * conversations table filtered by lead_id so new messages appear
 * without refresh.
 */

import { useEffect, useState } from 'react';
import { Bot, Check, User } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import { createBrowserClient } from '@/lib/supabase/client';
import { cn, relativeTime } from '@/lib/utils';
import type { ConversationMessage, ConversationRow, ConversationState } from '@/types/db';

// ------------------------------------------------------------------ helpers

function stateBadge(state: ConversationState) {
  const map: Record<ConversationState, { label: string; Icon: LucideIcon; cls: string }> = {
    active: {
      label: 'Auto',
      Icon: Bot,
      cls: 'bg-primary-container/60 text-on-primary-container',
    },
    handoff: {
      label: 'Operatore',
      Icon: User,
      cls: 'bg-tertiary-container/60 text-on-tertiary-container',
    },
    closed: {
      label: 'Chiusa',
      Icon: Check,
      cls: 'bg-surface-container-high text-on-surface-variant',
    },
  };
  return map[state] ?? map.active;
}

// ------------------------------------------------------------------ component

interface LeadConversationsCardProps {
  leadId: string;
  initialConversations: ConversationRow[];
}

export function LeadConversationsCard({
  leadId,
  initialConversations,
}: LeadConversationsCardProps) {
  const [conversations, setConversations] = useState(initialConversations);
  const [expanded, setExpanded] = useState<string | null>(
    initialConversations[0]?.id ?? null,
  );

  // Live updates via Supabase Realtime
  useEffect(() => {
    const sb = createBrowserClient();
    const channel = sb
      .channel(`conversations:lead:${leadId}`)
      .on(
        'postgres_changes',
        {
          event: '*',
          schema: 'public',
          table: 'conversations',
          filter: `lead_id=eq.${leadId}`,
        },
        (payload) => {
          const updated = payload.new as ConversationRow;
          setConversations((prev) => {
            const exists = prev.some((c) => c.id === updated.id);
            if (exists) {
              return prev.map((c) => (c.id === updated.id ? updated : c));
            }
            return [updated, ...prev];
          });
        },
      )
      .subscribe();

    return () => {
      void sb.removeChannel(channel);
    };
  }, [leadId]);

  if (conversations.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-outline-variant/40 px-6 py-8 text-center">
        <p className="text-sm text-on-surface-variant">
          Nessuna conversazione WhatsApp ancora.
        </p>
        <p className="mt-1 text-xs text-on-surface-variant">
          Il lead può avviarne una dal portale cliccando il bottone WhatsApp.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {conversations.map((conv) => {
        const badge = stateBadge(conv.state);
        const isOpen = expanded === conv.id;
        const lastMsg = conv.messages?.[conv.messages.length - 1];

        return (
          <div
            key={conv.id}
            className="overflow-hidden rounded-xl border border-outline-variant/30 bg-surface-container-lowest"
          >
            {/* Header */}
            <button
              type="button"
              onClick={() => setExpanded(isOpen ? null : conv.id)}
              className="flex w-full items-center justify-between px-4 py-3 text-left hover:bg-surface-container/50 transition-colors"
            >
              <div className="flex items-center gap-3">
                <span className="text-lg">💬</span>
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-on-surface">
                      WhatsApp · {maskPhone(conv.whatsapp_phone)}
                    </span>
                    <span
                      className={cn(
                        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold',
                        badge.cls,
                      )}
                    >
                      <badge.Icon size={10} strokeWidth={2.5} aria-hidden />
                      {badge.label}
                    </span>
                  </div>
                  <p className="mt-0.5 text-xs text-on-surface-variant">
                    {conv.turn_count} messagg{conv.turn_count === 1 ? 'io' : 'i'}
                    {conv.last_message_at &&
                      ` · ${relativeTime(conv.last_message_at)}`}
                    {lastMsg && (
                      <> · <span className="italic">
                        {lastMsg.content.slice(0, 50)}
                        {lastMsg.content.length > 50 ? '…' : ''}
                      </span></>
                    )}
                  </p>
                </div>
              </div>
              <span className="text-on-surface-variant">
                {isOpen ? '▲' : '▼'}
              </span>
            </button>

            {/* Thread */}
            {isOpen && (
              <div className="border-t border-outline-variant/20 bg-[#ece5dd]/20 px-4 py-4">
                <MessageThread messages={conv.messages ?? []} />

                {conv.state === 'handoff' && (
                  <div className="mt-3 rounded-lg border border-tertiary/20 bg-tertiary-container/20 px-3 py-2 text-xs text-on-tertiary-container">
                    🤝 Handoff attivo — il consulente continua la conversazione
                    direttamente su WhatsApp.
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ Thread

function MessageThread({ messages }: { messages: ConversationMessage[] }) {
  if (messages.length === 0) {
    return (
      <p className="text-xs text-on-surface-variant italic">
        Nessun messaggio ancora.
      </p>
    );
  }

  return (
    <div className="space-y-2 max-h-96 overflow-y-auto">
      {messages.map((msg, i) => {
        if (msg.role === 'system') {
          return (
            <div key={i} className="text-center">
              <span className="inline-block rounded-full bg-surface-container px-3 py-0.5 text-[10px] text-on-surface-variant italic">
                {msg.content}
              </span>
            </div>
          );
        }

        const isLead = msg.role === 'lead';
        return (
          <div
            key={i}
            className={cn(
              'flex',
              isLead ? 'justify-start' : 'justify-end',
            )}
          >
            <div
              className={cn(
                'max-w-[78%] rounded-2xl px-3 py-2 text-sm shadow-sm',
                isLead
                  ? 'rounded-tl-sm bg-white text-on-surface'
                  : 'rounded-tr-sm bg-primary-container/60 text-on-primary-container',
              )}
            >
              <p className="leading-snug">{msg.content}</p>
              <p
                className={cn(
                  'mt-1 text-[10px]',
                  isLead ? 'text-right text-on-surface-variant/60' : 'text-right text-on-primary-container/60',
                )}
              >
                {formatMsgTime(msg.ts)}
                {!isLead && (
                  <span className="ml-1 font-semibold text-[9px] opacity-70">
                    AI
                  </span>
                )}
              </p>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ------------------------------------------------------------------ utils

function maskPhone(phone: string): string {
  if (!phone || phone.length < 8) return phone;
  return `+${phone.slice(0, 2)} *** **** ${phone.slice(-3)}`;
}

function formatMsgTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString('it-IT', {
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return '';
  }
}
