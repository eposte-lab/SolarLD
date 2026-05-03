'use client';

/**
 * Subscribes to `events` INSERTs for the current tenant via Supabase
 * Realtime and surfaces a small rolling toast stack.
 *
 * Requires the `events` table to be part of the `supabase_realtime`
 * publication (run once per environment):
 *
 *   ALTER PUBLICATION supabase_realtime ADD TABLE events;
 *
 * Without that, subscriptions silently succeed but no payloads arrive.
 * The toast UI degrades gracefully — missing events just means no
 * banner, never an error.
 */

import { useEffect, useState } from 'react';

import { createBrowserClient } from '@/lib/supabase/client';

interface Toast {
  id: string;
  title: string;
  subtitle: string;
  type: 'outreach' | 'engagement' | 'conversion' | 'default';
  created_at: string;
}

/** Maps event_type → pretty title + visual accent.
 *  Exported for unit testing — do not import outside this module. */
export function classify(
  event_type: string,
  payload: Record<string, unknown> | null,
): Pick<Toast, 'title' | 'subtitle' | 'type'> {
  if (event_type === 'lead.outreach_sent')
    return { title: 'Outreach inviata', subtitle: event_type, type: 'outreach' };
  if (event_type.startsWith('lead.followup_sent'))
    return {
      title: `Follow-up ${event_type.split('_').pop()} inviato`,
      subtitle: event_type,
      type: 'outreach',
    };
  if (event_type === 'lead.portal_visited')
    return { title: 'Lead ha aperto il portal', subtitle: event_type, type: 'engagement' };
  if (event_type === 'lead.whatsapp_click')
    return { title: 'Click su WhatsApp', subtitle: event_type, type: 'engagement' };
  if (event_type === 'lead.appointment_requested')
    return {
      title: '🔥 Richiesta di contatto ricevuta',
      // The contact-form submission is the single highest-intent
      // signal the prospect can give us — they explicitly asked the
      // operator to call back. Surface contact name + phone in the
      // toast subtitle so the operator can decide on first glance
      // whether to reach out immediately.
      subtitle: [
        payload?.contact_name as string | undefined,
        payload?.contact_phone as string | undefined,
      ]
        .filter(Boolean)
        .join(' · ') || event_type,
      type: 'conversion',
    };
  if (event_type === 'lead.bolletta_uploaded')
    return {
      title: 'Bolletta caricata sul portale',
      subtitle: (() => {
        const kwh = payload?.ocr_kwh_yearly as number | undefined;
        const eur = payload?.ocr_eur_yearly as number | undefined;
        if (kwh && eur)
          return `${Math.round(kwh).toLocaleString('it-IT')} kWh/anno · €${Math.round(eur).toLocaleString('it-IT')}`;
        if (kwh) return `${Math.round(kwh).toLocaleString('it-IT')} kWh/anno`;
        return event_type;
      })(),
      type: 'engagement',
    };
  if (event_type === 'lead.optout_requested')
    return { title: 'Opt-out ricevuto', subtitle: event_type, type: 'default' };
  // Pixart postal tracking — paired with the TrackingAgent Pixart branch.
  if (event_type === 'lead.postal_printed')
    return { title: 'Cartolina stampata', subtitle: event_type, type: 'outreach' };
  if (event_type === 'lead.postal_shipped')
    return { title: 'Cartolina spedita', subtitle: event_type, type: 'outreach' };
  if (event_type === 'lead.postal_delivered')
    return { title: 'Cartolina consegnata', subtitle: event_type, type: 'outreach' };
  if (event_type === 'lead.postal_returned')
    return { title: 'Cartolina tornata al mittente', subtitle: event_type, type: 'default' };
  return { title: event_type, subtitle: event_type, type: 'default' };
}

const ACCENT: Record<Toast['type'], string> = {
  outreach: 'border-l-sky-500',
  engagement: 'border-l-indigo-500',
  conversion: 'border-l-emerald-500',
  default: 'border-l-zinc-500',
};

export function RealtimeToaster({ tenantId }: { tenantId: string }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  useEffect(() => {
    const supabase = createBrowserClient();
    const channel = supabase
      .channel(`events:${tenantId}`)
      .on(
        'postgres_changes',
        {
          event: 'INSERT',
          schema: 'public',
          table: 'events',
          filter: `tenant_id=eq.${tenantId}`,
        },
        (msg) => {
          const row = msg.new as {
            id: string;
            event_type: string;
            payload: Record<string, unknown> | null;
            created_at: string;
          };
          const { title, subtitle, type } = classify(row.event_type, row.payload);
          const toast: Toast = {
            id: row.id,
            title,
            subtitle,
            type,
            created_at: row.created_at,
          };
          setToasts((prev) => [toast, ...prev].slice(0, 5));
          // Auto-dismiss after 6s.
          setTimeout(() => {
            setToasts((prev) => prev.filter((t) => t.id !== toast.id));
          }, 6000);
        },
      )
      .subscribe();

    return () => {
      supabase.removeChannel(channel);
    };
  }, [tenantId]);

  if (toasts.length === 0) return null;

  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[320px] flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`pointer-events-auto rounded-md border border-border border-l-4 bg-card p-3 shadow-lg animate-in slide-in-from-bottom ${ACCENT[t.type]}`}
        >
          <p className="text-sm font-semibold">{t.title}</p>
          <p className="truncate text-xs text-muted-foreground">{t.subtitle}</p>
        </div>
      ))}
    </div>
  );
}
