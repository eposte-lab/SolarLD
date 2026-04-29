/**
 * Territories — CRUD + scan trigger.
 *
 * Layout (Luminous Curator bento):
 *   - Editorial header with counters (total / priority / excluded)
 *   - Two-column bento row:
 *       * Add-form bento — type/code/name/priority/excluded + optional bbox
 *       * Table bento — existing rows + delete + "Avvia scansione" per row
 *
 * Pipeline primer (rendered as inline alert):
 *   Territorio → Scansione (Hunter) → Tetti → Scoring → Lead → Outreach
 *
 * Mutations use server actions — no client JS, pure server component.
 */

import { ArrowRight, Lock } from 'lucide-react';
import { redirect } from 'next/navigation';

import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { TerritoryAddForm } from '@/components/territory-add-form';
import { TerritoryTable } from '@/components/territories/territory-table';
import {
  listTerritories,
  listScanSummaries,
  summariseTerritories,
  type ScanSummary,
} from '@/lib/data/territories';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { cn } from '@/lib/utils';
import type { TerritoryRow } from '@/types/db';

export const dynamic = 'force-dynamic';

// ---------------------------------------------------------------------------
// Display constants
// ---------------------------------------------------------------------------

const ERROR_COPY: Record<string, string> = {
  missing_code: 'Il campo "codice" è obbligatorio.',
  missing_name: 'Il campo "nome" è obbligatorio.',
  invalid_cap: 'Un CAP italiano deve essere composto da 5 cifre.',
  duplicate: 'Hai già registrato un territorio con questo tipo e codice.',
  missing_id: 'ID territorio non valido.',
  no_bbox:
    'Questo territorio non ha un\'area geografica — eliminalo e ri-aggiungilo ' +
    'usando il tasto "Rileva zona" nel form.',
  scan_failed:
    'La scansione non è partita — il worker arq non ha risposto. ' +
    'Controlla che il servizio worker sia attivo su Railway e che REDIS_URL sia configurato.',
  scan_no_auth: 'Sessione scaduta. Ricarica la pagina e riprova.',
  budget_exceeded:
    'Budget mensile di scansione esaurito (€150 per piano Founding). ' +
    'Attendi il mese prossimo o chiedi un upgrade del piano.',
  api_unreachable:
    'Impossibile raggiungere il server API. ' +
    'Controlla che NEXT_PUBLIC_API_URL punti al tuo servizio Railway ' +
    '(es. https://tuo-progetto.up.railway.app).',
  territory_locked:
    'Zona di esclusiva bloccata da contratto. ' +
    'Contatta il supporto per richiederne la modifica.',
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

type Search = Promise<{
  created?: string;
  deleted?: string;
  scanning?: string;
  error?: string;
}>;

export default async function TerritoriesPage({
  searchParams,
}: {
  searchParams: Search;
}) {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const sp = await searchParams;
  const rows = await listTerritories();
  const summary = summariseTerritories(rows);
  const scanSummaries = await listScanSummaries(rows.map((r) => r.id));
  const flash = buildFlash(sp, rows, scanSummaries);

  // Territorial exclusivity: once the installer has confirmed their
  // zone at the end of onboarding, the add-form and delete buttons
  // disappear and a lock banner takes their place. Scan button stays
  // enabled — the user can still run the funnel, just not widen/shrink.
  const isLocked = Boolean(ctx.tenant.territory_locked_at);

  return (
    <div className="space-y-6">
      <Header tenantName={ctx.tenant.business_name} summary={summary} />

      {/* Pipeline explainer — always visible, compact */}
      <PipelineBanner />

      {isLocked && <LockBanner lockedAt={ctx.tenant.territory_locked_at!} />}
      {flash && <FlashBanner flash={flash} />}

      {isLocked ? (
        <BentoCard span="full" padding="tight">
          {rows.length === 0 ? (
            <EmptyState />
          ) : (
            <TerritoryTable
              rows={rows}
              scanSummaries={scanSummaries}
              isLocked
            />
          )}
        </BentoCard>
      ) : (
        <BentoGrid cols={3}>
          <BentoCard span="1x1" variant="default">
            <TerritoryAddForm />
          </BentoCard>

          <BentoCard span="2x1" padding="tight">
            {rows.length === 0 ? (
              <EmptyState />
            ) : (
              <TerritoryTable rows={rows} scanSummaries={scanSummaries} />
            )}
          </BentoCard>
        </BentoGrid>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Lock banner
// ---------------------------------------------------------------------------

function LockBanner({ lockedAt }: { lockedAt: string }) {
  const when = (() => {
    try {
      return new Date(lockedAt).toLocaleDateString('it-IT', {
        year: 'numeric',
        month: 'long',
        day: '2-digit',
      });
    } catch {
      return lockedAt;
    }
  })();
  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-3 rounded-xl bg-primary-container px-5 py-3 text-sm font-semibold text-on-primary-container shadow-ambient-sm"
    >
      <Lock size={14} strokeWidth={2.25} aria-hidden className="shrink-0" />
      <span>
        Zona di esclusiva confermata il <strong>{when}</strong>. Per
        modificare i territori contatta il supporto.
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pipeline banner
// ---------------------------------------------------------------------------

function PipelineBanner() {
  const steps = [
    { label: 'Territorio', note: 'auto-coordinate', active: true },
    { label: 'Hunter', note: 'scansiona tetti' },
    { label: 'Tetti', note: 'roofs table' },
    { label: 'Scoring', note: 'crea lead' },
    { label: 'Creative', note: 'rendering' },
    { label: 'Outreach', note: 'email' },
  ];

  return (
    <div className="flex flex-wrap items-center gap-0 rounded-xl bg-surface-container-lowest px-5 py-3 shadow-ambient-sm">
      <p className="mr-4 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
        Pipeline
      </p>
      {steps.map((s, i) => (
        <div key={s.label} className="flex items-center">
          {i > 0 && (
            <ArrowRight
              size={11}
              strokeWidth={2}
              className="mx-1.5 text-on-surface-variant/40"
              aria-hidden
            />
          )}
          <div className="flex flex-col items-center">
            <span
              className={cn(
                'text-xs font-semibold',
                s.active ? 'text-primary' : 'text-on-surface',
              )}
            >
              {s.label}
            </span>
            <span className="text-[9px] text-on-surface-variant">{s.note}</span>
          </div>
        </div>
      ))}
      <p className="ml-auto text-[11px] text-on-surface-variant">
        Aggiungi un territorio → clicca{' '}
        <strong className="font-semibold">Scansiona</strong> per far
        partire Hunter.
      </p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Header
// ---------------------------------------------------------------------------

function Header({
  tenantName,
  summary,
}: {
  tenantName: string;
  summary: { total: number; priority: number; excluded: number };
}) {
  return (
    <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Copertura · {tenantName}
        </p>
        <h1 className="mt-1 font-headline text-4xl font-bold tracking-tighter text-on-surface md:text-5xl">
          Territori
        </h1>
        <p className="mt-2 max-w-lg text-sm text-on-surface-variant">
          Aggiungi un territorio — le coordinate vengono rilevate
          automaticamente. Poi avvia la scansione: Hunter popola la
          tabella dei tetti, Scoring crea i lead.
        </p>
      </div>

      <dl className="grid grid-cols-3 gap-6 rounded-xl bg-surface-container-lowest px-5 py-3 shadow-ambient-sm">
        <SummaryChip label="Totale" value={summary.total} />
        <SummaryChip label="Priorità alta" value={summary.priority} />
        <SummaryChip label="Esclusi" value={summary.excluded} />
      </dl>
    </div>
  );
}

function SummaryChip({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
        {label}
      </dt>
      <dd className="mt-0.5 font-headline text-2xl font-bold tabular-nums">
        {value}
      </dd>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Flash
// ---------------------------------------------------------------------------

type Flash = { tone: 'ok' | 'warn' | 'err'; msg: string };

function buildFlash(
  sp: Awaited<Search>,
  rows: TerritoryRow[],
  scanSummaries: Map<string, ScanSummary>,
): Flash | null {
  if (sp.scanning) {
    const row = rows.find((r) => r.id === sp.scanning);
    const name = row?.name ?? sp.scanning;
    const summary = sp.scanning ? scanSummaries.get(sp.scanning) : undefined;

    // If a scan.completed event already landed for this territory show
    // the real outcome immediately instead of "Hunter sta lavorando…"
    if (summary) {
      if (summary.atoka_empty) {
        return {
          tone: 'warn',
          msg: `Scansione di "${name}" completata — 0 aziende trovate. `
            + `Possibile problema di configurazione del servizio di scoperta. `
            + `Contatta il supporto se il problema persiste.`,
        };
      }
      if (summary.leads_qualified === 0) {
        return {
          tone: 'warn',
          msg: `Scansione di "${name}" completata — 0 lead qualificati. `
            + `Prova ad ampliare il territorio o ad allentare i filtri in Impostazioni → Moduli → Sorgente.`,
        };
      }
      return {
        tone: 'ok',
        msg: `Scansione di "${name}" completata — ${summary.leads_qualified} lead qualificati trovati. Vai su Lead per vederli.`,
      };
    }

    return {
      tone: 'ok',
      msg: `Scansione di "${name}" avviata — Hunter sta lavorando in background. I lead arriveranno entro qualche minuto.`,
    };
  }
  if (sp.created) {
    const row = rows.find((r) => r.code === sp.created);
    const hasBbox = Boolean(row?.bbox);
    return {
      tone: hasBbox ? 'ok' : 'warn',
      msg: hasBbox
        ? `Territorio "${sp.created}" aggiunto. Puoi ora avviare la scansione.`
        : `Territorio "${sp.created}" aggiunto, ma senza bounding box. Eliminalo e ri-aggiungilo con le coordinate NE/SW per abilitare la scansione.`,
    };
  }
  if (sp.deleted) {
    return { tone: 'ok', msg: 'Territorio eliminato.' };
  }
  if (sp.error) {
    const canned = ERROR_COPY[sp.error];
    return { tone: 'err', msg: canned ?? sp.error };
  }
  return null;
}

function FlashBanner({ flash }: { flash: Flash }) {
  const styles = {
    ok: 'bg-primary-container text-on-primary-container',
    warn: 'bg-tertiary-container text-on-tertiary-container',
    err: 'bg-secondary-container text-on-secondary-container',
  };
  return (
    <div
      role="status"
      className={cn(
        'rounded-xl px-5 py-3 text-sm font-semibold shadow-ambient-sm',
        styles[flash.tone],
      )}
    >
      {flash.msg}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Empty state
// ---------------------------------------------------------------------------

function EmptyState() {
  return (
    <div className="flex h-full min-h-[280px] flex-col items-center justify-center gap-3 rounded-lg bg-surface-container-low p-12 text-center">
      <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
        Copertura vuota
      </p>
      <p className="max-w-sm text-sm text-on-surface-variant">
        Aggiungi il primo territorio — le coordinate vengono rilevate
        automaticamente. Poi avvia la scansione per far partire Hunter.
      </p>
    </div>
  );
}

