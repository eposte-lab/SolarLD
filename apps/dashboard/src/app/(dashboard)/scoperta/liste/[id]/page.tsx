'use client';

/**
 * /scoperta/liste/[id] — single saved list with paginated items.
 *
 * Shows the snapshotted Atoka payload for one list, plus header
 * metadata (name, description, search filter recap, item counts) and
 * a CSV export button. Paginated 50 rows/page.
 */

import {
  AlertTriangle,
  ArrowLeft,
  Building2,
  Download,
  Euro,
  Loader2,
  MapPin,
  Users,
} from 'lucide-react';
import Link from 'next/link';
import { use, useEffect, useState } from 'react';

import { BentoCard } from '@/components/ui/bento-card';
import { SectionEyebrow } from '@/components/ui/section-eyebrow';
import { SortableTh } from '@/components/ui/sortable-th';
import { useSortableData } from '@/hooks/use-sortable-data';
import {
  type ProspectList,
  type ProspectListItem,
  getProspectList,
} from '@/lib/data/prospector';
import { formatNumber, relativeTime } from '@/lib/utils';

const PAGE_SIZE = 50;

function csvEscape(v: unknown): string {
  if (v == null) return '';
  const s = String(v);
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

function downloadCsv(list: ProspectList, items: ProspectListItem[]) {
  const headers = [
    'vat_number',
    'legal_name',
    'ateco_code',
    'ateco_description',
    'employees',
    'revenue_eur',
    'hq_address',
    'hq_cap',
    'hq_city',
    'hq_province',
    'website_domain',
    'decision_maker_name',
    'decision_maker_role',
    'decision_maker_email',
    'linkedin_url',
  ] as const;
  const rows = [
    headers.join(','),
    ...items.map((it) =>
      headers
        .map((h) => csvEscape((it as unknown as Record<string, unknown>)[h]))
        .join(','),
    ),
  ];
  const blob = new Blob([rows.join('\n')], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${list.name.replace(/\W+/g, '_')}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export default function ListDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);

  const [list, setList] = useState<ProspectList | null>(null);
  const [items, setItems] = useState<ProspectListItem[]>([]);
  const [itemsTotal, setItemsTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Note: sort is applied to the current page only (PAGE_SIZE=50).
  const { sorted: sortedItems, sortKey, sortDir, requestSort } = useSortableData<
    ProspectListItem,
    'name' | 'ateco' | 'sede' | 'employees' | 'revenue'
  >(items, (it, key) => {
    switch (key) {
      case 'name':
        return it.legal_name ?? '';
      case 'ateco':
        return it.ateco_code ?? '';
      case 'sede':
        return it.hq_city ?? '';
      case 'employees':
        return it.employees ?? null;
      case 'revenue':
        return it.revenue_eur ?? null;
    }
  });

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getProspectList(id, { page, page_size: PAGE_SIZE })
      .then((res) => {
        if (cancelled) return;
        setList(res.list);
        setItems(res.items);
        setItemsTotal(res.items_total);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : 'Errore');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [id, page]);

  const totalPages = Math.max(1, Math.ceil(itemsTotal / PAGE_SIZE));

  if (loading && !list) {
    return (
      <BentoCard variant="default" padding="loose" className="text-center">
        <Loader2 size={24} className="mx-auto animate-spin text-on-surface-variant" />
        <p className="mt-3 text-sm text-on-surface-variant">Carico lista…</p>
      </BentoCard>
    );
  }

  if (error || !list) {
    return (
      <BentoCard variant="default" padding="loose">
        <div className="flex items-start gap-2 text-error">
          <AlertTriangle size={20} strokeWidth={1.75} />
          <div>
            <p className="font-semibold">Impossibile caricare la lista</p>
            <p className="mt-1 text-sm text-on-surface-variant">
              {error ?? 'Lista non trovata.'}
            </p>
            <Link
              href="/scoperta/liste"
              className="mt-3 inline-flex items-center gap-1.5 text-sm font-semibold text-primary hover:underline"
            >
              <ArrowLeft size={14} strokeWidth={2.25} /> Torna alle liste
            </Link>
          </div>
        </div>
      </BentoCard>
    );
  }

  const filter = (list.search_filter ?? {}) as Record<string, unknown>;

  return (
    <div className="space-y-6">
      {/* Header */}
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <Link
            href="/scoperta/liste"
            className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-widest text-on-surface-variant hover:text-on-surface"
          >
            <ArrowLeft size={12} strokeWidth={2.25} /> Liste salvate
          </Link>
          <h1 className="mt-2 font-headline text-4xl font-bold tracking-tighter text-on-surface">
            {list.name}
          </h1>
          <p className="mt-1 text-sm text-on-surface-variant">
            {formatNumber(list.item_count)} aziende · creata{' '}
            {relativeTime(list.created_at)}
          </p>
          {list.description && (
            <p className="mt-1 max-w-2xl text-sm text-on-surface-variant">
              {list.description}
            </p>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => downloadCsv(list, items)}
            disabled={items.length === 0}
            className="inline-flex items-center gap-2 rounded-full bg-surface-container-high px-4 py-2 text-sm font-semibold text-on-surface transition-opacity hover:opacity-80 disabled:opacity-40"
          >
            <Download size={14} strokeWidth={2.25} /> Esporta pagina (CSV)
          </button>
        </div>
      </header>

      {/* Filter recap */}
      <BentoCard variant="muted" padding="default">
        <SectionEyebrow tone="dim">Filtri della ricerca</SectionEyebrow>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-4">
          <FilterCell label="Preset" value={(filter.preset_code as string | null) ?? '—'} />
          <FilterCell
            label="ATECO"
            value={
              Array.isArray(filter.ateco_codes)
                ? (filter.ateco_codes as string[]).join(', ')
                : '—'
            }
          />
          <FilterCell label="Provincia" value={(filter.province_code as string | null) ?? '—'} />
          <FilterCell label="Regione" value={(filter.region_code as string | null) ?? '—'} />
          <FilterCell
            label="Dipendenti"
            value={rangeLabel(filter.employees_min, filter.employees_max)}
          />
          <FilterCell
            label="Fatturato"
            value={rangeLabel(filter.revenue_min_eur, filter.revenue_max_eur, '€')}
          />
          <FilterCell label="Keyword" value={(filter.keyword as string | null) ?? '—'} />
        </dl>
      </BentoCard>

      {/* Items table */}
      <BentoCard variant="default" padding="default">
        <div className="-mx-2 overflow-x-auto">
          <table className="w-full min-w-[820px] border-separate border-spacing-y-1 text-sm">
            <thead>
              <tr>
                <SortableTh sortKey="name" active={sortKey} dir={sortDir} onSort={requestSort} className="px-2 py-2">Azienda</SortableTh>
                <SortableTh sortKey="ateco" active={sortKey} dir={sortDir} onSort={requestSort} className="px-2 py-2">ATECO</SortableTh>
                <SortableTh sortKey="sede" active={sortKey} dir={sortDir} onSort={requestSort} className="px-2 py-2">Sede</SortableTh>
                <SortableTh sortKey="employees" active={sortKey} dir={sortDir} onSort={requestSort} className="px-2 py-2" align="right">Dipendenti</SortableTh>
                <SortableTh sortKey="revenue" active={sortKey} dir={sortDir} onSort={requestSort} className="px-2 py-2" align="right">Fatturato</SortableTh>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-2 py-10 text-center text-sm text-on-surface-variant">
                    <Building2 size={28} className="mx-auto mb-2 opacity-40" strokeWidth={1.5} />
                    Lista vuota.
                  </td>
                </tr>
              )}
              {sortedItems.map((it) => (
                <tr
                  key={it.id}
                  className="bg-surface-container-low/60 transition-colors hover:bg-surface-container"
                >
                  <td className="px-2 py-3 align-top">
                    <div className="font-semibold text-on-surface">{it.legal_name}</div>
                    <div className="text-[11px] uppercase tracking-wide text-on-surface-muted">
                      P.IVA {it.vat_number}
                    </div>
                    {it.website_domain && (
                      <a
                        href={`https://${it.website_domain}`}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[11px] text-primary hover:underline"
                      >
                        {it.website_domain}
                      </a>
                    )}
                  </td>
                  <td className="px-2 py-3 align-top">
                    <div className="font-mono text-xs text-on-surface">
                      {it.ateco_code || '—'}
                    </div>
                    {it.ateco_description && (
                      <div className="text-[11px] text-on-surface-variant line-clamp-2">
                        {it.ateco_description}
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-3 align-top">
                    {it.hq_city ? (
                      <div className="flex items-start gap-1 text-on-surface">
                        <MapPin size={12} strokeWidth={1.75} className="mt-0.5 shrink-0 text-on-surface-muted" />
                        <div>
                          <div>
                            {it.hq_city}
                            {it.hq_province && (
                              <span className="text-on-surface-muted"> · {it.hq_province}</span>
                            )}
                          </div>
                          {it.hq_cap && (
                            <div className="text-[11px] text-on-surface-muted">
                              CAP {it.hq_cap}
                            </div>
                          )}
                        </div>
                      </div>
                    ) : (
                      <span className="text-on-surface-muted">—</span>
                    )}
                  </td>
                  <td className="px-2 py-3 text-right align-top tabular-nums">
                    {it.employees != null ? (
                      <span className="inline-flex items-center gap-1 text-on-surface">
                        <Users size={12} strokeWidth={1.75} className="text-on-surface-muted" />
                        {formatNumber(it.employees)}
                      </span>
                    ) : (
                      <span className="text-on-surface-muted">—</span>
                    )}
                  </td>
                  <td className="px-2 py-3 text-right align-top tabular-nums">
                    {it.revenue_eur != null ? (
                      <span className="inline-flex items-center gap-1 text-on-surface">
                        <Euro size={12} strokeWidth={1.75} className="text-on-surface-muted" />
                        {formatNumber(it.revenue_eur)}
                      </span>
                    ) : (
                      <span className="text-on-surface-muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {totalPages > 1 && (
          <div className="mt-4 flex items-center justify-between text-sm">
            <p className="text-on-surface-variant">
              Pagina {page} di {totalPages} · {formatNumber(itemsTotal)} aziende
            </p>
            <div className="flex items-center gap-2">
              <button
                type="button"
                disabled={page <= 1}
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                className="rounded-full bg-surface-container-high px-3 py-1.5 text-sm font-semibold text-on-surface transition-opacity hover:opacity-80 disabled:opacity-40"
              >
                ← Prec
              </button>
              <button
                type="button"
                disabled={page >= totalPages}
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                className="rounded-full bg-surface-container-high px-3 py-1.5 text-sm font-semibold text-on-surface transition-opacity hover:opacity-80 disabled:opacity-40"
              >
                Succ →
              </button>
            </div>
          </div>
        )}
      </BentoCard>
    </div>
  );
}

function FilterCell({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-muted">
        {label}
      </dt>
      <dd className="text-sm text-on-surface">{value || '—'}</dd>
    </div>
  );
}

function rangeLabel(
  min: unknown,
  max: unknown,
  prefix = '',
): string {
  const m = min != null && min !== '' ? `${prefix}${formatNumber(Number(min))}` : null;
  const M = max != null && max !== '' ? `${prefix}${formatNumber(Number(max))}` : null;
  if (m && M) return `${m} – ${M}`;
  if (m) return `≥ ${m}`;
  if (M) return `≤ ${M}`;
  return '—';
}
