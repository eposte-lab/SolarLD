'use client';

/**
 * /scoperta/liste/[id] — single saved list (v3 Places-based).
 *
 * Header + filter recap + items table + two action panels:
 *   1. Convalida per fotovoltaico — runs L2-L4 funnel inline per item
 *   2. Lancia outreach — promotes accepted items to leads + queues
 *      creative + outreach (passes through daily cap)
 *
 * Items table shows the Places enrichment + a `validation_status`
 * chip per row. Polling on /validate/status while a run is in flight
 * (started_at set, completed_at null).
 */

import {
  AlertTriangle,
  ArrowLeft,
  Building2,
  CheckCircle2,
  Clock,
  Loader2,
  MapPin,
  Send,
  ShieldCheck,
  Star,
  XCircle,
} from 'lucide-react';
import Link from 'next/link';
import { use, useCallback, useEffect, useMemo, useState } from 'react';

import { BentoCard } from '@/components/ui/bento-card';
import { GradientButton } from '@/components/ui/gradient-button';
import { SectionEyebrow } from '@/components/ui/section-eyebrow';
import {
  type ProspectList,
  type ProspectListItem,
  type ValidateStatusResponse,
  getProspectList,
  getValidateStatus,
  launchOutreachForList,
  validateProspectList,
} from '@/lib/data/prospector';
import { sectorLabel } from '@/lib/sector-labels';
import { cn, formatNumber, relativeTime } from '@/lib/utils';

const PAGE_SIZE = 50;
const STATUS_POLL_MS = 8000;

const STATUS_LABELS: Record<string, string> = {
  pending: 'In attesa',
  validating: 'In convalida',
  accepted: 'Tetto idoneo',
  rejected: 'Rifiutato',
  no_building: 'Nessun edificio',
  api_error: 'Errore API',
  skipped: 'Skip',
};

const STATUS_STYLES: Record<string, string> = {
  pending: 'bg-surface-container-high text-on-surface-variant',
  validating: 'bg-tertiary-container/60 text-on-tertiary-container',
  accepted: 'bg-primary-container text-on-primary-container',
  rejected: 'bg-secondary-container text-on-secondary-container',
  no_building: 'bg-surface-container-highest text-on-surface-variant',
  api_error: 'bg-error-container/40 text-on-error-container',
  skipped: 'bg-surface-container text-on-surface-variant opacity-70',
};

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

  const [validateStatus, setValidateStatus] =
    useState<ValidateStatusResponse | null>(null);
  const [validating, setValidating] = useState(false);
  const [launching, setLaunching] = useState(false);
  const [launchMsg, setLaunchMsg] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const res = await getProspectList(id, { page, page_size: PAGE_SIZE });
      setList(res.list);
      setItems(res.items);
      setItemsTotal(res.items_total);
      setError(null);
      try {
        const vs = await getValidateStatus(id);
        setValidateStatus(vs);
      } catch {
        // Status endpoint may not be reachable for legacy Atoka lists; tolerate.
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Errore');
    } finally {
      setLoading(false);
    }
  }, [id, page]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    refresh().catch(() => {
      if (!cancelled) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  // Poll while a validation is in flight.
  const validationInFlight =
    validateStatus !== null &&
    validateStatus.started_at !== null &&
    validateStatus.completed_at === null;

  useEffect(() => {
    if (!validationInFlight) return;
    const t = setInterval(() => {
      refresh().catch(() => {
        /* keep polling */
      });
    }, STATUS_POLL_MS);
    return () => clearInterval(t);
  }, [validationInFlight, refresh]);

  const counts = useMemo(() => {
    if (validateStatus) return validateStatus.by_status;
    const c: Record<string, number> = {};
    for (const it of items) {
      const s = it.validation_status ?? 'pending';
      c[s] = (c[s] ?? 0) + 1;
    }
    return c;
  }, [validateStatus, items]);

  const pendingCount = counts['pending'] ?? 0;
  const validatingCount = counts['validating'] ?? 0;
  const acceptedCount = counts['accepted'] ?? 0;

  async function onValidate() {
    setValidating(true);
    try {
      await validateProspectList(id);
      // Optimistically mark started so the polling kicks in.
      setValidateStatus((prev) => ({
        list_id: id,
        started_at: new Date().toISOString(),
        completed_at: null,
        item_count: prev?.item_count ?? itemsTotal,
        by_status: prev?.by_status ?? {},
      }));
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Errore convalida');
    } finally {
      setValidating(false);
    }
  }

  async function onLaunchOutreach() {
    if (acceptedCount === 0) return;
    if (
      !window.confirm(
        `Stai per promuovere ${acceptedCount} contatti a lead e mettere in coda l'outreach. Il cap giornaliero potrà differire alcuni invii al giorno successivo. Procedere?`,
      )
    ) {
      return;
    }
    setLaunching(true);
    setLaunchMsg(null);
    try {
      const res = await launchOutreachForList(id);
      setLaunchMsg(
        res.queued
          ? 'Lancio in coda — gli outreach partiranno secondo cap + send window.'
          : 'Job già accodato in precedenza.',
      );
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Errore lancio outreach');
    } finally {
      setLaunching(false);
    }
  }

  const totalPages = Math.max(1, Math.ceil(itemsTotal / PAGE_SIZE));

  if (loading && !list) {
    return (
      <BentoCard variant="default" padding="loose" className="text-center">
        <Loader2
          size={24}
          className="mx-auto animate-spin text-on-surface-variant"
        />
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
  const isPlacesList = list.source === 'places';

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
            {list.source && (
              <>
                {' · '}
                <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest">
                  {list.source}
                </span>
              </>
            )}
          </p>
          {list.description && (
            <p className="mt-1 max-w-2xl text-sm text-on-surface-variant">
              {list.description}
            </p>
          )}
        </div>
      </header>

      {/* v3 actions panel — visible only for Places-based lists */}
      {isPlacesList && (
        <BentoCard variant="default" padding="default" span="full">
          <SectionEyebrow tone="dim">Pipeline on-demand</SectionEyebrow>
          <div className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2">
            {/* Step 1: Convalida */}
            <div className="rounded-lg bg-surface-container-low p-4">
              <div className="flex items-center gap-2">
                <ShieldCheck size={16} className="text-tertiary" />
                <p className="text-sm font-semibold text-on-surface">
                  1. Convalida per fotovoltaico
                </p>
              </div>
              <p className="mt-2 text-xs text-on-surface-variant">
                Esegue scraping web + Google Solar API + filtro qualità su
                ogni candidato. Restituisce un verdetto «tetto idoneo» o
                rifiuto motivato.
              </p>
              <div className="mt-3 flex items-center justify-between">
                <div className="text-[11px] text-on-surface-variant">
                  {validationInFlight ? (
                    <span className="inline-flex items-center gap-1.5 text-tertiary">
                      <Clock size={12} className="animate-pulse" /> In corso…
                    </span>
                  ) : validateStatus?.completed_at ? (
                    <span className="inline-flex items-center gap-1.5 text-success">
                      <CheckCircle2 size={12} /> Completata{' '}
                      {relativeTime(validateStatus.completed_at)}
                    </span>
                  ) : pendingCount > 0 ? (
                    <span>{pendingCount} candidati da convalidare</span>
                  ) : (
                    <span>Nessun candidato in attesa</span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={onValidate}
                  disabled={
                    validating ||
                    validationInFlight ||
                    pendingCount + validatingCount === 0
                  }
                  className="inline-flex items-center gap-2 rounded-full bg-tertiary px-4 py-2 text-sm font-semibold text-on-tertiary transition-opacity hover:opacity-95 disabled:opacity-40"
                >
                  {validating ? 'Avvio…' : 'Avvia convalida'}
                </button>
              </div>
            </div>

            {/* Step 2: Outreach */}
            <div className="rounded-lg bg-surface-container-low p-4">
              <div className="flex items-center gap-2">
                <Send size={16} className="text-primary" />
                <p className="text-sm font-semibold text-on-surface">
                  2. Lancia rendering + outreach
                </p>
              </div>
              <p className="mt-2 text-xs text-on-surface-variant">
                Promuove i candidati con tetto idoneo a lead. Mette in coda
                rendering tetto + email outreach. Il cap giornaliero
                schedula automaticamente le eccedenze.
              </p>
              <div className="mt-3 flex items-center justify-between">
                <div className="text-[11px] text-on-surface-variant">
                  {acceptedCount > 0 ? (
                    <span className="inline-flex items-center gap-1.5 text-success">
                      <CheckCircle2 size={12} /> {acceptedCount} pronti
                    </span>
                  ) : (
                    <span>Convalida prima per produrre candidati idonei.</span>
                  )}
                </div>
                <button
                  type="button"
                  onClick={onLaunchOutreach}
                  disabled={
                    launching ||
                    acceptedCount === 0 ||
                    validationInFlight
                  }
                  className="inline-flex items-center gap-2 rounded-full bg-primary px-4 py-2 text-sm font-semibold text-on-primary transition-opacity hover:opacity-95 disabled:opacity-40"
                >
                  {launching ? 'Avvio…' : 'Lancia outreach'}
                </button>
              </div>
              {launchMsg && (
                <p className="mt-2 text-[11px] text-on-surface-variant">
                  {launchMsg}
                </p>
              )}
            </div>
          </div>

          {/* Status counts strip */}
          {Object.keys(counts).length > 0 && (
            <div className="mt-4 flex flex-wrap gap-2">
              {Object.entries(counts).map(([k, v]) => (
                <span
                  key={k}
                  className={cn(
                    'inline-flex items-center gap-1 rounded-full px-3 py-1 text-[11px] font-semibold',
                    STATUS_STYLES[k] ?? 'bg-surface-container-high text-on-surface-variant',
                  )}
                >
                  {STATUS_LABELS[k] ?? k}: {v}
                </span>
              ))}
            </div>
          )}
        </BentoCard>
      )}

      {/* Filter recap */}
      <BentoCard variant="muted" padding="default">
        <SectionEyebrow tone="dim">Filtri della ricerca</SectionEyebrow>
        <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm md:grid-cols-4">
          <FilterCell
            label="Settore"
            value={
              typeof filter.sector === 'string'
                ? sectorLabel(filter.sector as string)
                : '—'
            }
          />
          <FilterCell
            label="Provincia"
            value={(filter.province_code as string | null) ?? '—'}
          />
          <FilterCell
            label="Comune"
            value={(filter.comune as string | null) ?? '—'}
          />
          <FilterCell
            label="Raggio"
            value={
              filter.radius_km != null ? `${filter.radius_km} km` : '—'
            }
          />
          <FilterCell
            label="Keyword"
            value={(filter.keyword as string | null) ?? '—'}
          />
        </dl>
      </BentoCard>

      {/* Items table */}
      <BentoCard variant="default" padding="default">
        <div className="-mx-2 overflow-x-auto">
          <table className="w-full min-w-[820px] border-separate border-spacing-y-1 text-sm">
            <thead>
              <tr className="text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                <th className="px-2 py-2">Azienda</th>
                <th className="px-2 py-2">Indirizzo</th>
                <th className="px-2 py-2 text-center">Rating</th>
                <th className="px-2 py-2">Stato</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 && (
                <tr>
                  <td
                    colSpan={4}
                    className="px-2 py-10 text-center text-sm text-on-surface-variant"
                  >
                    <Building2
                      size={28}
                      className="mx-auto mb-2 opacity-40"
                      strokeWidth={1.5}
                    />
                    Lista vuota.
                  </td>
                </tr>
              )}
              {items.map((it) => (
                <tr
                  key={it.id}
                  className="bg-surface-container-low/60 transition-colors hover:bg-surface-container"
                >
                  <td className="px-2 py-3 align-top">
                    <div className="font-semibold text-on-surface">
                      {it.legal_name}
                    </div>
                    {it.google_maps_uri && (
                      <a
                        href={it.google_maps_uri}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[11px] text-primary hover:underline"
                      >
                        Google Maps ↗
                      </a>
                    )}
                    {it.website_domain && (
                      <a
                        href={
                          it.website_domain.startsWith('http')
                            ? it.website_domain
                            : `https://${it.website_domain}`
                        }
                        target="_blank"
                        rel="noreferrer"
                        className="ml-2 text-[11px] text-primary hover:underline"
                      >
                        sito ↗
                      </a>
                    )}
                  </td>
                  <td className="px-2 py-3 align-top">
                    {it.hq_address ? (
                      <div className="flex items-start gap-1 text-on-surface-variant">
                        <MapPin
                          size={12}
                          strokeWidth={1.75}
                          className="mt-0.5 shrink-0"
                        />
                        <span className="text-xs">{it.hq_address}</span>
                      </div>
                    ) : (
                      <span className="text-on-surface-variant">—</span>
                    )}
                  </td>
                  <td className="px-2 py-3 text-center align-top">
                    {it.rating != null ? (
                      <span className="inline-flex items-center gap-1 text-xs text-on-surface">
                        <Star size={10} className="fill-warning text-warning" />
                        {it.rating.toFixed(1)}
                        {it.user_ratings_total != null && (
                          <span className="text-on-surface-variant">
                            {' '}
                            ({it.user_ratings_total})
                          </span>
                        )}
                      </span>
                    ) : (
                      <span className="text-xs text-on-surface-variant">—</span>
                    )}
                  </td>
                  <td className="px-2 py-3 align-top">
                    <StatusChip status={it.validation_status} />
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

function StatusChip({ status }: { status: string }) {
  const label = STATUS_LABELS[status] ?? status;
  const style = STATUS_STYLES[status] ?? STATUS_STYLES.pending;
  const Icon =
    status === 'accepted'
      ? CheckCircle2
      : status === 'rejected' || status === 'no_building' || status === 'api_error'
        ? XCircle
        : status === 'validating'
          ? Loader2
          : Clock;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-semibold',
        style,
      )}
    >
      <Icon
        size={10}
        className={status === 'validating' ? 'animate-spin' : undefined}
      />
      {label}
    </span>
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
