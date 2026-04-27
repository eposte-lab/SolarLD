'use client';

/**
 * NotificationsBell — topbar bell with a dropdown of recent items.
 *
 * Client component because the dropdown state + the "mark read"
 * mutations are interactive. Data is seeded from SSR via the
 * `initialUnread` / `initialItems` props so the first paint has
 * accurate counts without a request roundtrip; thereafter the
 * component refetches from `/v1/notifications` via Supabase
 * directly (service-role key never leaves the backend — we rely on
 * RLS + auth session cookies from `@supabase/ssr`).
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { createBrowserClient } from '@supabase/ssr';

import { cn } from '@/lib/utils';
import type { NotificationRow } from '@/lib/data/notifications';

const SEVERITY_DOT: Record<NotificationRow['severity'], string> = {
  info: 'bg-on-surface-variant',
  success: 'bg-success',
  warning: 'bg-primary',
  error: 'bg-error',
};

function timeAgo(iso: string): string {
  const diffMin = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (diffMin < 1) return 'ora';
  if (diffMin < 60) return `${diffMin}m`;
  const h = Math.round(diffMin / 60);
  if (h < 24) return `${h}h`;
  const d = Math.round(h / 24);
  return `${d}g`;
}

export interface NotificationsBellProps {
  initialUnread: number;
  initialItems: NotificationRow[];
  tenantId: string;
}

export function NotificationsBell({
  initialUnread,
  initialItems,
  tenantId,
}: NotificationsBellProps) {
  const [open, setOpen] = useState(false);
  const [unread, setUnread] = useState(initialUnread);
  const [items, setItems] = useState<NotificationRow[]>(initialItems);

  const supabase = useMemo(
    () =>
      createBrowserClient(
        process.env.NEXT_PUBLIC_SUPABASE_URL!,
        process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
      ),
    [],
  );

  // Subscribe to INSERTs for this tenant so new bells arrive live.
  useEffect(() => {
    const channel = supabase
      .channel(`notifications:${tenantId}`)
      .on(
        'postgres_changes',
        {
          event: 'INSERT',
          schema: 'public',
          table: 'notifications',
          filter: `tenant_id=eq.${tenantId}`,
        },
        (evt) => {
          const row = evt.new as NotificationRow;
          setItems((prev) => [row, ...prev].slice(0, 20));
          setUnread((c) => c + 1);
        },
      )
      .subscribe();
    return () => {
      supabase.removeChannel(channel);
    };
  }, [supabase, tenantId]);

  const markAllRead = useCallback(async () => {
    const ids = items.filter((i) => !i.read_at).map((i) => i.id);
    if (ids.length === 0) return;
    const { error } = await supabase
      .from('notifications')
      .update({ read_at: new Date().toISOString() })
      .in('id', ids);
    if (error) return;
    setItems((prev) =>
      prev.map((i) => (ids.includes(i.id) ? { ...i, read_at: new Date().toISOString() } : i)),
    );
    setUnread(0);
  }, [items, supabase]);

  return (
    <div className="relative">
      <button
        aria-label="Notifiche"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          'relative flex h-10 w-10 items-center justify-center rounded-full',
          'bg-surface-container-lowest ghost-border transition-colors',
          'hover:bg-surface-container-low',
        )}
      >
        <svg
          viewBox="0 0 24 24"
          fill="currentColor"
          className="h-5 w-5 text-on-surface"
        >
          <path d="M12 22a2 2 0 002-2h-4a2 2 0 002 2zm6-6V11a6 6 0 10-12 0v5l-2 2v1h16v-1l-2-2z" />
        </svg>
        {unread > 0 && (
          <span
            className={cn(
              'absolute -right-0.5 -top-0.5 flex h-5 min-w-[20px] items-center justify-center',
              'rounded-full bg-primary px-1 text-[10px] font-bold text-on-primary',
            )}
          >
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>

      {open && (
        <>
          <button
            aria-label="Chiudi notifiche"
            className="fixed inset-0 z-40 cursor-default"
            onClick={() => setOpen(false)}
          />
          <div
            className={cn(
              'absolute right-0 top-12 z-50 w-[360px] max-h-[480px] overflow-hidden',
              'rounded-2xl glass-panel shadow-ambient',
            )}
            role="dialog"
          >
            <header className="flex items-center justify-between border-b border-white/8 px-4 py-3">
              <p className="font-headline text-sm font-bold tracking-tighter">
                Notifiche
              </p>
              <button
                onClick={markAllRead}
                disabled={unread === 0}
                className={cn(
                  'text-xs font-semibold',
                  unread === 0
                    ? 'text-on-surface-variant opacity-50'
                    : 'text-primary hover:underline',
                )}
              >
                Segna tutte come lette
              </button>
            </header>

            <div className="max-h-[420px] overflow-y-auto">
              {items.length === 0 ? (
                <div className="p-8 text-center text-sm text-on-surface-variant">
                  Nessuna notifica
                </div>
              ) : (
                <ul>
                  {items.map((n) => {
                    const content = (
                      <div
                        className={cn(
                          'flex gap-3 px-4 py-3 transition-colors hover:bg-white/5',
                          !n.read_at && 'bg-primary/8',
                        )}
                      >
                        <span
                          className={cn(
                            'mt-1.5 h-2 w-2 shrink-0 rounded-full',
                            SEVERITY_DOT[n.severity],
                          )}
                        />
                        <div className="min-w-0 flex-1">
                          <p className="font-semibold text-sm text-on-surface">
                            {n.title}
                          </p>
                          {n.body && (
                            <p className="mt-0.5 line-clamp-2 text-xs text-on-surface-variant">
                              {n.body}
                            </p>
                          )}
                          <p className="mt-1 text-[10px] uppercase tracking-widest text-on-surface-variant">
                            {timeAgo(n.created_at)}
                          </p>
                        </div>
                      </div>
                    );
                    return (
                      <li key={n.id} className="border-b border-surface-container-high last:border-0">
                        {n.href ? (
                          // href comes from the database at runtime, so
                          // we bypass next/link's typed-route check with
                          // a plain anchor — full page nav is acceptable
                          // for a notification deep-link.
                          <a href={n.href} onClick={() => setOpen(false)}>
                            {content}
                          </a>
                        ) : (
                          content
                        )}
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
