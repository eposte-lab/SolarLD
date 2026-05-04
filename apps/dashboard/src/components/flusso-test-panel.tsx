'use client';

/**
 * FlussoTestPanel — step-by-step pipeline test surface for /territorio.
 *
 * Shows the entire FLUSSO 1 v3 + FLUSSO 3 chain as a vertical stepper:
 *
 *   1. Configura territorio (settori + province)        — done in TerritorioConfig
 *   2. Mappa zone OSM (L0)                              — Rimappa il territorio
 *   3. Scansione candidati (L1→L5 + L6 promotion)       — Avvia scansione v3
 *   4. Asset generation (rendering image/video)         — auto + manual
 *   5. Outreach inviato                                 — auto + manual
 *
 * Each step has inline action buttons so the operator never needs to scroll
 * to a separate card to trigger the next stage.
 *
 * Polls /v1/territory/scan-results every 8s while a scan is in flight
 * (summary.is_running = true). Stops polling on completion or unmount.
 */

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';

import {
  getScanResults,
  mapTerritory,
  runFunnelManual,
  type ScanResultsResponse,
} from '@/lib/data/territory';

interface FlussoTestPanelProps {
  initialData: ScanResultsResponse | null;
  /** Number of OSM zones already mapped (from L0). Hides Step 3+ if 0. */
  zoneCount: number;
  /** Number of sectors covered (Step 1 status). */
  sectorCount: number;
}

const POLL_INTERVAL_MS = 8000;

export function FlussoTestPanel({
  initialData,
  zoneCount,
  sectorCount,
}: FlussoTestPanelProps) {
  const [data, setData] = useState<ScanResultsResponse | null>(initialData);
  const [polling, setPolling] = useState(false);

  // ---- Inline action state (Step 2 + 3) ----
  const [remapBusy, setRemapBusy] = useState(false);
  const [remapMsg, setRemapMsg] = useState<string | null>(null);
  const [remapErr, setRemapErr] = useState<string | null>(null);

  const [scanBusy, setScanBusy] = useState(false);
  const [scanMsg, setScanMsg] = useState<string | null>(null);
  const [scanErr, setScanErr] = useState<string | null>(null);
  const [maxCandidates, setMaxCandidates] = useState(50);

  // Track local zone count so Remap updates the step immediately.
  const [localZoneCount, setLocalZoneCount] = useState(zoneCount);

  // After a manual scan trigger, start polling even before is_running arrives.
  const pollAfterTrigger = useRef(false);

  async function handleRemap() {
    setRemapBusy(true);
    setRemapMsg(null);
    setRemapErr(null);
    try {
      const res = await mapTerritory();
      setRemapMsg(
        `Mappatura avviata (job ${res.job_id.slice(0, 8)}…) — ` +
          `settori: ${res.wizard_groups.join(', ') || '—'} · ` +
          `province: ${res.province_codes.join(', ') || '—'}.`,
      );
    } catch (e) {
      setRemapErr(e instanceof Error ? e.message : 'map_failed');
    } finally {
      setRemapBusy(false);
    }
  }

  async function handleStartScan() {
    setScanBusy(true);
    setScanMsg(null);
    setScanErr(null);
    try {
      const res = await runFunnelManual({ max_l1_candidates: maxCandidates });
      setScanMsg(
        `Scansione avviata (job ${res.job_id.slice(0, 8)}…) — ` +
          `${res.zone_count} zone · max ${res.max_l1_candidates} candidati. ` +
          `Attendi 5-20 min poi aggiorna.`,
      );
      setLocalZoneCount(res.zone_count);
      // Start polling even if is_running hasn't flipped yet.
      pollAfterTrigger.current = true;
      setPolling(true);
    } catch (e) {
      setScanErr(e instanceof Error ? e.message : 'funnel_failed');
    } finally {
      setScanBusy(false);
    }
  }

  // Polling: re-fetch /scan-results while the API reports is_running=true
  // OR we just triggered a scan manually (pollAfterTrigger).
  useEffect(() => {
    const isRunning = data?.summary?.is_running ?? false;
    if (!isRunning && !pollAfterTrigger.current) return;

    setPolling(true);
    const id = setInterval(async () => {
      try {
        const next = await getScanResults();
        setData(next);
        if (!next.summary.is_running) {
          pollAfterTrigger.current = false;
          setPolling(false);
        }
      } catch {
        // network blip — keep polling
      }
    }, POLL_INTERVAL_MS);
    return () => {
      clearInterval(id);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data?.summary?.is_running, polling]);

  const summary = data?.summary;
  const l1 = summary?.l1_candidates ?? 0;
  const recommended = summary?.l5_recommended ?? 0;
  const leadsCreated = summary?.l6_leads_created ?? 0;
  const leadsRendering = summary?.leads_with_rendering ?? 0;
  const leadsOutreach = summary?.leads_outreach_sent ?? 0;

  // Step states — derived top-down so a later step is "waiting" if any
  // upstream prerequisite is missing.
  const step1Done = sectorCount > 0;
  const step2Done = (localZoneCount ?? zoneCount) > 0;
  const step3Running = !!summary?.is_running || scanBusy;
  const step3Started = l1 > 0;
  // "never ran" = no started_at in the cost log
  const step3NeverRan = !summary?.started_at;
  const step3Done = !!summary?.completed_at && !summary?.is_running && l1 > 0;
  const step4Done = leadsRendering > 0;
  const step5Done = leadsOutreach > 0;

  const currentZoneCount = localZoneCount ?? zoneCount;

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Pipeline Test · Geocentrico v3
          </p>
          <h2 className="font-headline text-xl font-bold text-on-surface">
            Stato esecuzione step-by-step
          </h2>
        </div>
        {polling && (
          <span className="inline-flex items-center gap-2 rounded-full bg-primary-container px-3 py-1.5 text-xs font-semibold text-on-primary-container">
            <span className="relative flex h-1.5 w-1.5">
              <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-primary" />
            </span>
            Aggiornamento automatico ogni 8s
          </span>
        )}
      </header>

      <ol className="space-y-2.5">
        {/* ---- Step 1: Config ---- */}
        <Step
          n={1}
          title="Configurazione territorio"
          status={step1Done ? 'done' : 'waiting'}
          stat={step1Done ? `${sectorCount} settori` : 'Da configurare'}
          help={
            step1Done
              ? 'Settori e province salvati. Modifica nel pannello "Configurazione territorio" in alto.'
              : 'Apri "Configura territorio" e seleziona almeno un settore e una provincia.'
          }
        />

        {/* ---- Step 2: L0 mapping ---- */}
        <Step
          n={2}
          title="Mappatura zone OSM (L0)"
          status={step2Done ? 'done' : step1Done ? 'pending' : 'waiting'}
          stat={
            step2Done
              ? `${currentZoneCount.toLocaleString('it-IT')} zone industriali / commerciali`
              : 'Da avviare'
          }
          help={
            step2Done
              ? 'OSM Overpass ha trovato e classificato i poligoni. Ri-clicca per aggiornare.'
              : 'Avvia la mappatura: individua tutte le zone industriali / commerciali nella tua area. Tempo: 5-15 min.'
          }
        >
          <ActionRow>
            <button
              type="button"
              onClick={handleRemap}
              disabled={remapBusy || !step1Done}
              className="inline-flex items-center gap-1 rounded-full bg-surface-container-high px-3 py-1 text-xs font-semibold text-on-surface hover:bg-outline-variant disabled:opacity-50"
            >
              {remapBusy ? '⏳ Mappatura in corso…' : step2Done ? '↺ Rimappa il territorio' : '▶ Mappa il territorio'}
            </button>
          </ActionRow>
          {remapMsg && <p className="mt-1 text-xs text-success">{remapMsg}</p>}
          {remapErr && <p className="mt-1 text-xs text-error">Errore: {remapErr}</p>}
        </Step>

        {/* ---- Step 3: L1→L6 scan ---- */}
        <Step
          n={3}
          title="Scansione candidati (L1 → L6)"
          status={
            step3Done
              ? 'done'
              : step3Running
                ? 'running'
                : step3Started
                  ? 'running'
                  : step2Done
                    ? 'pending'
                    : 'waiting'
          }
          stat={
            summary && l1 > 0
              ? `${l1} trovati · ${recommended} raccomandati · ${leadsCreated} lead creati`
              : 'Da avviare'
          }
          help={
            !step2Done
              ? 'Prima completa la mappatura zone OSM (Step 2).'
              : step3Running
                ? 'Funnel in esecuzione: Places → Scraping → Qualità edificio → Solar API → Haiku. Attendi 5-20 min.'
                : step3NeverRan || l1 === 0
                  ? 'Avvia la scansione: scopre le aziende target nelle zone mappate via Google Places, le arricchisce e le punteggia.'
                  : 'Scansione completata. Puoi rilanciare per un secondo giro.'
          }
        >
          {step2Done && (
            <>
              <ActionRow>
                <button
                  type="button"
                  onClick={handleStartScan}
                  disabled={scanBusy || step3Running}
                  className="inline-flex items-center gap-1 rounded-full bg-primary px-3 py-1 text-xs font-semibold text-on-primary hover:bg-primary/90 disabled:opacity-50"
                >
                  {scanBusy || step3Running
                    ? '⏳ Scansione in corso…'
                    : step3Done
                      ? '↺ Riavvia scansione v3'
                      : '▶ Avvia scansione v3'}
                </button>
                <select
                  value={maxCandidates}
                  onChange={(e) => setMaxCandidates(Number(e.target.value))}
                  disabled={scanBusy || step3Running}
                  className="rounded-md border border-outline-variant bg-surface-container px-2 py-1 text-xs text-on-surface disabled:opacity-50"
                >
                  <option value={50}>50 candidati (~€0.15, test)</option>
                  <option value={100}>100 candidati (~€0.30, pilota)</option>
                  <option value={250}>250 candidati (~€0.75)</option>
                  <option value={500}>500 candidati (~€1.50)</option>
                  <option value={1000}>1000 candidati (~€3.00)</option>
                </select>
              </ActionRow>
              {scanMsg && <p className="mt-1 text-xs text-success">{scanMsg}</p>}
              {scanErr && <p className="mt-1 text-xs text-error">Errore: {scanErr}</p>}
            </>
          )}
          {summary && l1 > 0 && (
            <SubWaterfall summary={summary} />
          )}
        </Step>

        {/* ---- Step 4: Asset generation ---- */}
        <Step
          n={4}
          title="Asset generation (rendering)"
          status={
            step4Done
              ? 'done'
              : leadsCreated > 0
                ? 'pending'
                : 'waiting'
          }
          stat={
            leadsCreated > 0
              ? `${leadsRendering} di ${leadsCreated} lead con rendering`
              : 'Da generare'
          }
          help={
            leadsCreated === 0
              ? 'Servono lead generati da L6 (vedi step 3).'
              : leadsRendering === 0
                ? 'Il cron pick-up nelle prossime 30 min creerà rendering image + video. Per testarlo subito apri un lead e premi "Rigenera rendering".'
                : 'Rendering generato (Replicate / Kling). Apri il lead per vederlo.'
          }
        >
          {leadsCreated > 0 && (
            <ActionRow>
              <Link
                href="/leads?tier=hot"
                className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-xs font-semibold text-primary hover:bg-primary/20"
              >
                Apri {leadsCreated} lead →
              </Link>
            </ActionRow>
          )}
        </Step>

        {/* ---- Step 5: Outreach ---- */}
        <Step
          n={5}
          title="Outreach inviato"
          status={
            step5Done
              ? 'done'
              : leadsRendering > 0
                ? 'pending'
                : 'waiting'
          }
          stat={
            leadsCreated > 0
              ? `${leadsOutreach} di ${leadsCreated} lead contattati`
              : '0'
          }
          help={
            leadsRendering === 0
              ? 'Servono lead con rendering (vedi step 4).'
              : leadsOutreach === 0
                ? 'Il cron giornaliero invia automaticamente. Per testare subito apri un lead e premi "Invia outreach".'
                : 'Email partite. Le metriche di apertura/click compaiono in /deliverability.'
          }
        >
          {leadsRendering > 0 && (
            <ActionRow>
              <Link
                href="/deliverability"
                className="inline-flex items-center gap-1 rounded-full bg-primary/10 px-3 py-1 text-xs font-semibold text-primary hover:bg-primary/20"
              >
                Vedi deliverability →
              </Link>
            </ActionRow>
          )}
        </Step>
      </ol>

      <p className="text-[11px] leading-relaxed text-on-surface-variant">
        <strong>In produzione</strong> i passi 2–5 girano in automatico via
        cron (L0 + funnel ogni notte; rendering e outreach quando il lead
        entra in <code className="rounded bg-surface-container-low px-1 py-px">ready_to_send</code>).
        Su questa pagina puoi forzare manualmente ogni step per validare il
        comportamento end-to-end.
      </p>
    </section>
  );
}

// ---------------------------------------------------------------------------
// Building blocks
// ---------------------------------------------------------------------------

type StepStatus = 'done' | 'running' | 'pending' | 'waiting';

const STATUS_DOT: Record<StepStatus, { className: string; icon: string }> = {
  done: { className: 'bg-primary text-on-primary', icon: '✓' },
  running: {
    className: 'bg-amber-400 text-amber-950 animate-pulse',
    icon: '◐',
  },
  pending: { className: 'bg-surface-container-high text-on-surface', icon: '·' },
  waiting: { className: 'bg-surface-container-low text-on-surface-variant', icon: '·' },
};

const STATUS_LABEL: Record<StepStatus, string> = {
  done: 'Completato',
  running: 'In esecuzione',
  pending: 'Pronto',
  waiting: 'In attesa',
};

function Step({
  n,
  title,
  status,
  stat,
  help,
  children,
}: {
  n: number;
  title: string;
  status: StepStatus;
  stat: string;
  help: string;
  children?: React.ReactNode;
}) {
  const dot = STATUS_DOT[status];
  return (
    <li className="flex gap-3 rounded-2xl bg-surface-container-low p-4 ring-1 ring-on-surface/5">
      {/* Status badge */}
      <div className="flex flex-col items-center gap-1">
        <span
          className={`flex h-7 w-7 items-center justify-center rounded-full text-sm font-bold ${dot.className}`}
        >
          {status === 'done' ? dot.icon : n}
        </span>
        <span className="text-[9px] font-semibold uppercase tracking-widest text-on-surface-variant">
          {STATUS_LABEL[status]}
        </span>
      </div>

      {/* Body */}
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h3 className="font-headline text-sm font-bold text-on-surface">
            {title}
          </h3>
          <span className="font-headline text-base font-bold tabular-nums text-on-surface">
            {stat}
          </span>
        </div>
        <p className="text-xs leading-relaxed text-on-surface-variant">{help}</p>
        {children}
      </div>
    </li>
  );
}

function ActionRow({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-wrap items-center gap-2 pt-1.5">{children}</div>;
}

// ---------------------------------------------------------------------------
// Inline mini-waterfall (compact L1→L5 for the step body)
// ---------------------------------------------------------------------------

const STAGE_LABELS = [
  { key: 'l1_candidates', label: 'L1 Places' },
  { key: 'l2_with_email', label: 'L2 Scraping' },
  { key: 'l3_accepted', label: 'L3 Qualità' },
  { key: 'l4_solar_accepted', label: 'L4 Solar' },
  { key: 'l5_recommended', label: 'L5 Score' },
] as const;

function SubWaterfall({
  summary,
}: {
  summary: NonNullable<ScanResultsResponse['summary']>;
}) {
  const max = summary.l1_candidates || 1;
  return (
    <div className="mt-2 space-y-1 rounded-lg bg-surface px-3 py-2">
      {STAGE_LABELS.map(({ key, label }) => {
        const count = summary[key] as number;
        const pct = Math.max(2, Math.round((count / max) * 100));
        return (
          <div key={key} className="flex items-center gap-2">
            <span className="w-20 shrink-0 text-[10px] uppercase tracking-wide text-on-surface-variant">
              {label}
            </span>
            <div className="relative h-3 flex-1 overflow-hidden rounded-sm bg-surface-container-low">
              <div
                className="absolute inset-y-0 left-0 bg-primary/70"
                style={{ width: `${pct}%` }}
                aria-hidden
              />
            </div>
            <span className="w-12 text-right font-mono text-[11px] font-semibold text-on-surface">
              {count.toLocaleString('it-IT')}
            </span>
          </div>
        );
      })}
      {summary.total_cost_eur > 0 && (
        <p className="pt-1 text-[10px] uppercase tracking-widest text-on-surface-variant">
          Costo totale ·{' '}
          <span className="font-semibold text-on-surface">
            €{summary.total_cost_eur.toFixed(2)}
          </span>
        </p>
      )}
    </div>
  );
}
