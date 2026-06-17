'use client';

/**
 * Super-admin tenant activity log — a detailed, read-only chronology of
 * everything that happens for a tenant. Replaces the approval queue with full
 * transparency (the moderation gate stays on only for old un-promoted leads).
 *
 * Reads GET /v1/admin/tenants/{id}/activity-log (events table, enriched with
 * business name + render image). Category filter + search are client-side over
 * the loaded page; "carica altri" pages the server by offset.
 */

import {
  AlertTriangle,
  Flame,
  History,
  Image as ImageIcon,
  Loader2,
  MousePointerClick,
  Radar,
  Search,
  Send,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';

import { api } from '@/lib/api-client';

type ActivityItem = {
  occurred_at: string | null;
  event_type: string;
  event_source: string | null;
  lead_id: string | null;
  business_name: string | null;
  rendering_image_url: string | null;
  payload: Record<string, unknown> | null;
};

type ActivityResponse = { items: ActivityItem[]; total: number };

type Category = 'send' | 'engagement' | 'conversion' | 'error' | 'render' | 'system';

const CATEGORY: Record<
  Category,
  { label: string; color: string; Icon: typeof Send }
> = {
  send: { label: 'Invii', color: '#378ADD', Icon: Send },
  engagement: { label: 'Reazioni', color: '#1D9E75', Icon: MousePointerClick },
  conversion: { label: 'Conversioni', color: '#BA7517', Icon: Flame },
  error: { label: 'Errori', color: '#E24B4A', Icon: AlertTriangle },
  render: { label: 'Render', color: '#7F77DD', Icon: ImageIcon },
  system: { label: 'Sistema', color: '#888780', Icon: Radar },
};

const CATEGORY_ORDER: Category[] = ['send', 'engagement', 'conversion', 'error', 'render', 'system'];

function categorize(t: string): Category {
  if (t.includes('outreach_failed') || t.includes('render_skipped') || t.includes('optout')) {
    return 'error';
  }
  if (t.includes('appointment') || t.includes('conversion') || t.includes('contract_signed')) {
    return 'conversion';
  }
  if (t === 'lead.rendered') return 'render';
  if (t.startsWith('moderation.')) return 'system';
  if (t.startsWith('scan.') || t.startsWith('hunter.') || t.startsWith('tracking.') || t.startsWith('subject.') || t.startsWith('compliance.')) {
    return 'system';
  }
  if (
    t.includes('opened') ||
    t.includes('clicked') ||
    t.includes('portal') ||
    t.includes('whatsapp') ||
    t.includes('reply') ||
    t.includes('bolletta')
  ) {
    return 'engagement';
  }
  return 'send';
}

const LABELS: Record<string, string> = {
  'lead.outreach_sent': 'Email inviata',
  'lead.contacted': 'Primo contatto',
  'lead.outreach_failed': 'Invio fallito',
  'lead.email_delivered': 'Email consegnata',
  'lead.email_opened': 'Email aperta',
  'lead.email_clicked': 'Link cliccato',
  'lead.portal_visited': 'Ha aperto la pagina personale',
  'lead.whatsapp_click': 'Click su WhatsApp',
  'lead.appointment_requested': 'Richiesta di contatto',
  'lead.bolletta_uploaded': 'Bolletta caricata',
  'lead.reply_received': 'Ha risposto',
  'lead.optout_requested': 'Disiscrizione',
  'lead.rendered': 'Rendering generato',
  'lead.render_skipped': 'Rendering saltato',
  'lead.scored': 'Punteggio calcolato',
  'moderation.lead.excluded': 'Lead escluso',
  'scan.completed': 'Scansione completata',
};

function label(t: string): string {
  return LABELS[t] ?? t.replace(/^(lead|portal|moderation|scan|hunter|tracking)\./, '').replace(/_/g, ' ');
}

function detail(item: ActivityItem): string | null {
  const p = item.payload ?? {};
  const reason = typeof p.failure_reason === 'string' ? p.failure_reason : null;
  if (item.event_type === 'lead.outreach_failed') {
    if (reason === 'no_mx_record') return 'Email non valida (no-MX) → coda telefono';
    return reason ? `Motivo: ${reason}` : null;
  }
  if (item.event_type === 'moderation.lead.excluded') {
    const r = typeof p.reason === 'string' ? p.reason : null;
    return r ? `Motivo: ${r}` : 'Escluso dall’operatore';
  }
  if (item.event_type === 'lead.email_clicked' && typeof p.link_url === 'string') {
    return p.link_url;
  }
  if (typeof p.channel === 'string') return `via ${p.channel}`;
  return null;
}

function fmtTime(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  const today = new Date();
  const sameDay =
    d.getDate() === today.getDate() &&
    d.getMonth() === today.getMonth() &&
    d.getFullYear() === today.getFullYear();
  const time = d.toLocaleTimeString('it-IT', { hour: '2-digit', minute: '2-digit' });
  if (sameDay) return time;
  return `${d.toLocaleDateString('it-IT', { day: '2-digit', month: '2-digit' })} ${time}`;
}

const PAGE = 100;

export function TenantActivityLog({ tenantId }: { tenantId: string }) {
  const [items, setItems] = useState<ActivityItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cat, setCat] = useState<Category | 'all'>('all');
  const [q, setQ] = useState('');

  const load = useCallback(
    async (nextOffset: number) => {
      setLoading(true);
      setError(null);
      try {
        const res = await api.get<ActivityResponse>(
          `/v1/admin/tenants/${encodeURIComponent(tenantId)}/activity-log?limit=${PAGE}&offset=${nextOffset}`,
        );
        setItems((prev) => (nextOffset === 0 ? res.items : [...prev, ...res.items]));
        setTotal(res.total);
        setOffset(nextOffset);
      } catch (e) {
        setError((e as { message?: string })?.message ?? 'Errore nel caricamento.');
      } finally {
        setLoading(false);
      }
    },
    [tenantId],
  );

  useEffect(() => {
    void load(0);
  }, [load]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return items.filter((it) => {
      if (cat !== 'all' && categorize(it.event_type) !== cat) return false;
      if (needle && !(it.business_name ?? '').toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [items, cat, q]);

  return (
    <section className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <History size={18} className="text-on-surface-variant" aria-hidden />
          <h2 className="font-headline text-lg font-bold tracking-tight text-on-surface">
            Cronologia
          </h2>
          <span className="text-xs text-on-surface-variant">{total} eventi</span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <FilterChip active={cat === 'all'} onClick={() => setCat('all')}>
          Tutti
        </FilterChip>
        {CATEGORY_ORDER.map((c) => (
          <FilterChip key={c} active={cat === c} onClick={() => setCat(c)} dot={CATEGORY[c].color}>
            {CATEGORY[c].label}
          </FilterChip>
        ))}
        <div className="flex min-w-[140px] flex-1 items-center gap-2 rounded-lg border border-outline-variant bg-surface-container-low px-3 py-1.5">
          <Search size={14} className="text-on-surface-variant" aria-hidden />
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="cerca azienda…"
            className="w-full bg-transparent text-xs text-on-surface outline-none placeholder:text-on-surface-variant"
          />
        </div>
      </div>

      {error && (
        <div className="rounded-lg border border-error/30 bg-error/10 px-3 py-2 text-sm text-on-surface">
          {error}
        </div>
      )}

      <div className="overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest">
        {filtered.length === 0 && !loading ? (
          <div className="p-10 text-center text-sm text-on-surface-variant">
            Nessun evento con questi filtri.
          </div>
        ) : (
          filtered.map((it, i) => {
            const c = CATEGORY[categorize(it.event_type)];
            const d = detail(it);
            return (
              <div
                key={`${it.occurred_at}-${it.event_type}-${i}`}
                className="flex items-start gap-3 border-b border-outline-variant/60 px-4 py-3 last:border-b-0"
              >
                <span className="min-w-[42px] pt-0.5 text-xs text-on-surface-variant">
                  {fmtTime(it.occurred_at)}
                </span>
                <span
                  className="flex h-7 w-7 flex-none items-center justify-center rounded-full"
                  style={{ backgroundColor: `${c.color}22`, color: c.color }}
                >
                  <c.Icon size={15} aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-on-surface">
                    {it.business_name ?? label(it.event_type)}
                  </div>
                  <div className="truncate text-[13px] text-on-surface-variant">
                    {it.business_name ? label(it.event_type) : (it.event_source ?? '')}
                    {d ? ` · ${d}` : ''}
                  </div>
                </div>
                {it.rendering_image_url && (
                  <a
                    href={it.rendering_image_url}
                    target="_blank"
                    rel="noreferrer"
                    className="block flex-none overflow-hidden rounded-md border border-outline-variant"
                    title="Apri il render dell’analisi"
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={it.rendering_image_url}
                      alt="render analisi"
                      className="h-[42px] w-[60px] object-cover"
                      loading="lazy"
                    />
                  </a>
                )}
              </div>
            );
          })
        )}
      </div>

      {items.length < total && (
        <div className="text-center">
          <button
            type="button"
            disabled={loading}
            onClick={() => void load(offset + PAGE)}
            className="inline-flex items-center gap-1.5 rounded-lg border border-outline-variant px-4 py-2 text-sm text-on-surface-variant transition-colors hover:bg-surface-container-low disabled:opacity-50"
          >
            {loading ? <Loader2 size={14} className="animate-spin" aria-hidden /> : null}
            carica altri ({total - items.length})
          </button>
        </div>
      )}
    </section>
  );
}

function FilterChip({
  active,
  onClick,
  dot,
  children,
}: {
  active: boolean;
  onClick: () => void;
  dot?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={
        'inline-flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs transition-colors ' +
        (active
          ? 'border-primary bg-primary/10 font-semibold text-on-surface'
          : 'border-outline-variant text-on-surface-variant hover:bg-surface-container-low')
      }
    >
      {dot && <span className="h-2 w-2 rounded-full" style={{ backgroundColor: dot }} aria-hidden />}
      {children}
    </button>
  );
}
