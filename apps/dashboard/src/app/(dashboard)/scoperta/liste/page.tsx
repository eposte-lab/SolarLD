'use client';

/**
 * /scoperta/liste — index of saved prospect lists.
 *
 * Shows everything the operator has snapshot from Atoka discovery,
 * most-recent first. Each row is a glass card with name, count, preset
 * chip, last-updated timestamp, and quick "Apri / Elimina" actions.
 */

import {
  ArrowRight,
  ListPlus,
  Loader2,
  Search,
  Trash2,
} from 'lucide-react';
import Link from 'next/link';
import { useEffect, useState } from 'react';

import { BentoCard } from '@/components/ui/bento-card';
import { GradientButton } from '@/components/ui/gradient-button';
import { SectionEyebrow } from '@/components/ui/section-eyebrow';
import {
  type ProspectList,
  deleteProspectList,
  listProspectLists,
} from '@/lib/data/prospector';
import { formatNumber, relativeTime } from '@/lib/utils';

export default function ListeIndexPage() {
  const [rows, setRows] = useState<ProspectList[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const res = await listProspectLists({ page: 1, page_size: 50 });
      setRows(res.rows);
      setTotal(res.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Errore');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  async function handleDelete(id: string, name: string) {
    if (!window.confirm(`Eliminare la lista "${name}"? L'azione cancella anche le aziende contenute.`)) {
      return;
    }
    setDeletingId(id);
    try {
      await deleteProspectList(id);
      setRows((prev) => prev.filter((r) => r.id !== id));
      setTotal((t) => Math.max(0, t - 1));
    } catch (err) {
      alert(err instanceof Error ? err.message : 'Errore eliminazione');
    } finally {
      setDeletingId(null);
    }
  }

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <SectionEyebrow tone="mint" icon={<ListPlus size={11} strokeWidth={2.25} />}>
            Liste salvate · {formatNumber(total)} totali
          </SectionEyebrow>
          <h1 className="mt-1.5 font-headline text-4xl font-bold tracking-tighter text-on-surface">
            Le mie liste
          </h1>
          <p className="mt-1 max-w-xl text-sm text-on-surface-variant">
            Snapshot delle ricerche Atoka. Ogni lista mantiene il payload
            originale per esportazione, riuso o promozione nel funnel.
          </p>
        </div>
        <GradientButton href="/scoperta" size="md">
          <Search size={16} strokeWidth={2.25} /> Nuova ricerca
        </GradientButton>
      </header>

      {loading && (
        <BentoCard variant="default" padding="loose" className="text-center">
          <Loader2 size={24} className="mx-auto animate-spin text-on-surface-variant" />
          <p className="mt-3 text-sm text-on-surface-variant">Carico liste…</p>
        </BentoCard>
      )}

      {!loading && error && (
        <BentoCard variant="default" padding="default">
          <p className="text-sm text-error">{error}</p>
        </BentoCard>
      )}

      {!loading && !error && rows.length === 0 && (
        <BentoCard variant="default" padding="loose" className="text-center">
          <ListPlus
            size={40}
            strokeWidth={1.4}
            className="mx-auto mb-3 text-on-surface-muted"
          />
          <h2 className="font-headline text-xl font-bold text-on-surface">
            Nessuna lista ancora
          </h2>
          <p className="mt-1 text-sm text-on-surface-variant">
            Vai su “Trova aziende” per cercare e salvare la tua prima lista.
          </p>
          <GradientButton href="/scoperta" size="md" className="mt-5">
            <Search size={16} strokeWidth={2.25} /> Avvia ricerca
          </GradientButton>
        </BentoCard>
      )}

      {!loading && rows.length > 0 && (
        <div className="grid gap-3">
          {rows.map((list) => (
            <BentoCard
              key={list.id}
              variant="default"
              padding="default"
              className="flex flex-wrap items-center justify-between gap-4"
            >
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="font-headline text-lg font-semibold text-on-surface">
                    {list.name}
                  </h3>
                  {list.preset_code && (
                    <span className="rounded-full bg-primary/15 px-2.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-primary">
                      {list.preset_code.replace(/_/g, ' ')}
                    </span>
                  )}
                </div>
                {list.description && (
                  <p className="mt-1 text-xs text-on-surface-variant">
                    {list.description}
                  </p>
                )}
                <p className="mt-1 text-[11px] uppercase tracking-widest text-on-surface-muted">
                  {formatNumber(list.item_count)} aziende ·{' '}
                  {list.imported_count > 0
                    ? `${formatNumber(list.imported_count)} importate · `
                    : ''}
                  creata {relativeTime(list.created_at)}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => handleDelete(list.id, list.name)}
                  disabled={deletingId === list.id}
                  className="inline-flex h-9 w-9 items-center justify-center rounded-full bg-surface-container-high text-on-surface-variant transition-colors hover:bg-error-container hover:text-on-error-container disabled:opacity-40"
                  title="Elimina lista"
                  aria-label="Elimina lista"
                >
                  {deletingId === list.id ? (
                    <Loader2 size={14} className="animate-spin" />
                  ) : (
                    <Trash2 size={14} strokeWidth={1.75} />
                  )}
                </button>
                <Link
                  href={`/scoperta/liste/${list.id}`}
                  className="inline-flex items-center gap-1.5 rounded-full bg-primary px-4 py-2 text-sm font-semibold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-95"
                >
                  Apri
                  <ArrowRight size={14} strokeWidth={2.25} />
                </Link>
              </div>
            </BentoCard>
          ))}
        </div>
      )}
    </div>
  );
}
