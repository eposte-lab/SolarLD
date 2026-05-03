'use client';

/**
 * Lead detail — on-demand LinkedIn enrichment button (Sprint 4.3).
 *
 * Triggers Proxycurl server-side via POST /v1/leads/{id}/enrich/linkedin.
 * Cache hit: free; cache miss: ~$0.01. Surface the cache_hit flag in the
 * UI so the operator knows when a paid call happened.
 */

import { useState } from 'react';
import { ExternalLink, Linkedin } from 'lucide-react';

import { enrichLeadLinkedIn, type LinkedInEnrichment } from '@/lib/data/linkedin';

interface Props {
  leadId: string;
  initial?: LinkedInEnrichment | null;
}

export function LeadLinkedInButton({ leadId, initial }: Props) {
  const [data, setData] = useState<LinkedInEnrichment | null>(initial ?? null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function handleClick(force = false) {
    setBusy(true);
    setErr(null);
    try {
      const out = await enrichLeadLinkedIn(leadId, { forceRefresh: force });
      setData(out);
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'enrich_failed');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-md border border-outline-variant bg-surface-container/50 p-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm font-semibold text-on-surface">
          <Linkedin className="h-4 w-4" />
          <span>LinkedIn (on-demand)</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => handleClick(false)}
            disabled={busy}
            className="rounded-full bg-primary px-3 py-1.5 text-xs font-semibold text-on-primary shadow-ambient-sm transition-colors hover:bg-primary/90 disabled:opacity-50"
          >
            {busy ? 'Cerco…' : data ? 'Aggiorna' : 'Cerca su LinkedIn'}
          </button>
          {data ? (
            <button
              type="button"
              onClick={() => handleClick(true)}
              disabled={busy}
              className="rounded-full border border-outline-variant px-3 py-1.5 text-xs text-on-surface-variant transition-colors hover:bg-surface-container-high disabled:opacity-50"
              title="Forza nuova chiamata Proxycurl (~$0.01)"
            >
              Force refresh
            </button>
          ) : null}
        </div>
      </div>

      {err ? (
        <p className="text-xs text-error">Errore: {err}</p>
      ) : null}

      {data && data.found ? (
        <div className="space-y-2 text-sm">
          {data.cache_hit ? (
            <p className="text-xs text-success">
              ✓ Da cache (no costi)
            </p>
          ) : (
            <p className="text-xs text-warning">
              Nuova chiamata Proxycurl (~$0.01)
            </p>
          )}

          <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs">
            <dt className="text-on-surface-variant">Nome</dt>
            <dd className="text-on-surface">{data.name ?? '—'}</dd>

            <dt className="text-on-surface-variant">Settore</dt>
            <dd className="text-on-surface">{data.industry ?? '—'}</dd>

            <dt className="text-on-surface-variant">Dipendenti</dt>
            <dd className="text-on-surface">{data.employee_count_range ?? '—'}</dd>

            <dt className="text-on-surface-variant">Anno fondazione</dt>
            <dd className="text-on-surface">{data.founded_year ?? '—'}</dd>

            <dt className="text-on-surface-variant">Sede</dt>
            <dd className="text-on-surface">
              {[data.hq_city, data.hq_country].filter(Boolean).join(', ') || '—'}
            </dd>
          </dl>

          {data.description ? (
            <p className="rounded-md bg-surface-container-high p-3 text-xs text-on-surface-variant">
              {data.description}
            </p>
          ) : null}

          {data.linkedin_url ? (
            <a
              href={data.linkedin_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
            >
              Apri su LinkedIn
              <ExternalLink className="h-3 w-3" />
            </a>
          ) : null}
        </div>
      ) : data && !data.found ? (
        <p className="text-xs text-on-surface-variant">
          Nessuna corrispondenza su LinkedIn.{' '}
          {data.cache_hit ? '(cache)' : '(chiamata Proxycurl)'}
        </p>
      ) : (
        <p className="text-xs text-on-surface-variant">
          Clicca per cercare il profilo aziendale su LinkedIn (~$0.01 per
          chiamata, cached 60 giorni).
        </p>
      )}
    </div>
  );
}
