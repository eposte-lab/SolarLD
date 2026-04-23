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

import { redirect } from 'next/navigation';

import { BentoCard, BentoGrid } from '@/components/ui/bento-card';
import { TerritoryAddForm } from '@/components/territory-add-form';
import {
  listTerritories,
  listScanSummaries,
  summariseTerritories,
  type ScanSummary,
} from '@/lib/data/territories';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { cn } from '@/lib/utils';
import type { TerritoryRow, TerritoryType } from '@/types/db';

import { deleteTerritory, triggerScan } from './_actions';

export const dynamic = 'force-dynamic';

// ---------------------------------------------------------------------------
// Display constants
// ---------------------------------------------------------------------------

const TYPE_LABEL: Record<TerritoryType, string> = {
  cap: 'CAP',
  comune: 'Comune',
  provincia: 'Provincia',
  regione: 'Regione',
};

const ERROR_COPY: Record<string, string> = {
  missing_code: 'Il campo "codice" è obbligatorio.',
  missing_name: 'Il campo "nome" è obbligatorio.',
  invalid_cap: 'Un CAP italiano deve essere composto da 5 cifre.',
  duplicate: 'Hai già registrato un territorio con questo tipo e codice.',
  missing_id: 'ID territorio non valido.',
  no_bbox:
    'Questo territorio non ha un\'area geografica — eliminalo e ri-aggiungilo ' +
    'usando il tasto "📍 Rileva zona" nel form.',
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
};

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString('it-IT', {
      year: 'numeric',
      month: 'short',
      day: '2-digit',
    });
  } catch {
    return iso;
  }
}

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

  return (
    <div className="space-y-6">
      <Header tenantName={ctx.tenant.business_name} summary={summary} />

      {/* Pipeline explainer — always visible, compact */}
      <PipelineBanner />

      {flash && <FlashBanner flash={flash} />}

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
            <span className="mx-1.5 text-on-surface-variant opacity-40">→</span>
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
          msg: `Scansione di "${name}" completata — 0 aziende trovate da Atoka. `
            + `Causa più comune: ATOKA_API_KEY non configurata sul server API. `
            + `Controlla le variabili d'ambiente su Railway.`,
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

// ---------------------------------------------------------------------------
// Table
// ---------------------------------------------------------------------------

function TerritoryTable({
  rows,
  scanSummaries,
}: {
  rows: TerritoryRow[];
  scanSummaries: Map<string, ScanSummary>;
}) {
  return (
    <div className="overflow-hidden rounded-lg bg-surface-container-low">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            <th className="px-5 py-3">Nome</th>
            <th className="px-5 py-3">Tipo</th>
            <th className="px-5 py-3">Codice</th>
            <th className="px-5 py-3 text-right">Priorità</th>
            <th className="px-5 py-3">Bbox</th>
            <th className="px-5 py-3">Stato</th>
            <th className="px-5 py-3">Ultima scan</th>
            <th className="px-5 py-3">Aggiunto</th>
            <th className="px-5 py-3 text-right" />
          </tr>
        </thead>
        <tbody className="bg-surface-container-lowest">
          {rows.map((t, idx) => (
            <tr
              key={t.id}
              className="transition-colors hover:bg-surface-container-low"
              style={
                idx !== 0
                  ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }
                  : undefined
              }
            >
              <td className="px-5 py-4 font-semibold text-on-surface">
                {t.name}
              </td>
              <td className="px-5 py-4 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                {TYPE_LABEL[t.type]}
              </td>
              <td className="px-5 py-4 font-mono text-xs text-on-surface">
                {t.code}
              </td>
              <td className="px-5 py-4 text-right font-headline font-bold tabular-nums">
                {t.priority}
              </td>
              <td className="px-5 py-4">
                {t.bbox ? (
                  <BboxPreview bbox={t.bbox} />
                ) : (
                  <span className="text-[10px] font-semibold uppercase tracking-wide text-secondary">
                    Mancante
                  </span>
                )}
              </td>
              <td className="px-5 py-4">
                {t.excluded ? (
                  <Badge tone="muted">Escluso</Badge>
                ) : t.priority >= 7 ? (
                  <Badge tone="primary">Priorità alta</Badge>
                ) : (
                  <Badge tone="neutral">Attivo</Badge>
                )}
              </td>
              <td className="px-5 py-4">
                <LastScanBadge summary={scanSummaries.get(t.id)} />
              </td>
              <td className="px-5 py-4 text-xs text-on-surface-variant">
                {formatDate(t.created_at)}
              </td>
              <td className="px-5 py-4">
                <div className="flex items-center justify-end gap-3">
                  <ScanButton id={t.id} name={t.name} hasBbox={Boolean(t.bbox)} />
                  <DeleteButton id={t.id} name={t.name} />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function BboxPreview({
  bbox,
}: {
  bbox: NonNullable<TerritoryRow['bbox']>;
}) {
  return (
    <span className="font-mono text-[10px] text-on-surface-variant">
      {bbox.ne.lat.toFixed(3)},{bbox.ne.lng.toFixed(3)}
      <br />
      {bbox.sw.lat.toFixed(3)},{bbox.sw.lng.toFixed(3)}
    </span>
  );
}

function ScanButton({
  id,
  name,
  hasBbox,
}: {
  id: string;
  name: string;
  hasBbox: boolean;
}) {
  return (
    <form action={triggerScan} className="inline">
      <input type="hidden" name="id" value={id} />
      <input type="hidden" name="has_bbox" value={hasBbox ? '1' : '0'} />
      <button
        type="submit"
        disabled={!hasBbox}
        title={
          hasBbox
            ? `Avvia scansione tetti per ${name}`
            : 'Bbox mancante — elimina e ri-aggiungi il territorio con le coordinate'
        }
        className={cn(
          'rounded-full px-3 py-1 text-xs font-semibold transition-colors',
          hasBbox
            ? 'bg-primary-container text-on-primary-container hover:bg-primary/20'
            : 'cursor-not-allowed bg-surface-container text-on-surface-variant opacity-50',
        )}
        aria-label={`Avvia scansione ${name}`}
      >
        Scansiona
      </button>
    </form>
  );
}

function DeleteButton({ id, name }: { id: string; name: string }) {
  return (
    <form action={deleteTerritory} className="inline">
      <input type="hidden" name="id" value={id} />
      <button
        type="submit"
        className="text-xs font-semibold text-secondary hover:underline"
        aria-label={`Elimina ${name}`}
      >
        elimina
      </button>
    </form>
  );
}

// ---------------------------------------------------------------------------
// Badge
// ---------------------------------------------------------------------------

const BADGE_TONE = {
  primary: 'bg-primary-container text-on-primary-container',
  neutral: 'bg-surface-container-high text-on-surface',
  muted: 'bg-surface-container text-on-surface-variant',
} as const;

function Badge({
  tone,
  children,
}: {
  tone: keyof typeof BADGE_TONE;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-widest',
        BADGE_TONE[tone],
      )}
    >
      {children}
    </span>
  );
}

function LastScanBadge({ summary }: { summary?: ScanSummary }) {
  if (!summary) {
    return (
      <span className="text-[10px] text-on-surface-variant/50">
        Mai eseguita
      </span>
    );
  }

  const date = new Date(summary.occurred_at).toLocaleDateString('it-IT', {
    day: '2-digit',
    month: 'short',
  });

  if (summary.atoka_empty) {
    return (
      <span
        className="inline-flex flex-col gap-0.5"
        title="Atoka non ha trovato aziende — verifica ATOKA_API_KEY su Railway"
      >
        <span className="text-[10px] font-semibold text-error">
          ⚠ 0 aziende (Atoka)
        </span>
        <span className="text-[9px] text-on-surface-variant">{date}</span>
      </span>
    );
  }

  if (summary.leads_qualified === 0) {
    return (
      <span className="inline-flex flex-col gap-0.5">
        <span className="text-[10px] font-semibold text-on-surface-variant">
          0 lead
        </span>
        <span className="text-[9px] text-on-surface-variant">{date}</span>
      </span>
    );
  }

  return (
    <span className="inline-flex flex-col gap-0.5">
      <span className="text-[10px] font-semibold text-primary">
        ✓ {summary.leads_qualified} lead
      </span>
      <span className="text-[9px] text-on-surface-variant">{date}</span>
    </span>
  );
}
