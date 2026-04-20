'use client';

/**
 * ExperimentsManager — client component that manages the full A/B experiments
 * UX: list, create form, stats panel, end/declare winner actions.
 *
 * Data flow:
 *  - Initial list comes from SSR (passed as `initialRows` prop).
 *  - Stats are fetched lazily per-experiment via the API when the user
 *    expands a card (to avoid Bayesian Monte Carlo blocking SSR).
 *  - Create / patch / delete mutate via `api` client + `router.refresh()`.
 *  - AI variant generation via POST /v1/branding/generate-variants (B.13).
 */

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';

import { api, ApiError } from '@/lib/api-client';
import { cn, relativeTime } from '@/lib/utils';
import type {
  ExperimentRow,
  ExperimentStats,
  ExperimentVerdict,
} from '@/types/db';

// ---- AI variant types (mirrors branding.py) ----
interface AiVariant {
  subject: string;
  preheader: string;
  body_preview: string;
  rationale: string;
}
interface AiVariantsResponse {
  variants: AiVariant[];
  subject_type: string;
  tone: string;
}

interface Props {
  initialRows: ExperimentRow[];
}

type Phase = 'idle' | 'creating' | 'loading_stats' | 'patching';

// Pre-filled variant data from the AI picker passed into the form
interface PrefillData {
  subjA?: string;
  subjB?: string;
}

// ---------------------------------------------------------------------------

export function ExperimentsManager({ initialRows }: Props) {
  const router = useRouter();
  const [showCreate, setShowCreate] = useState(false);
  const [showTopAi, setShowTopAi] = useState(false);
  const [prefill, setPrefill] = useState<PrefillData>({});
  const [phase, setPhase] = useState<Phase>('idle');
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [statsCache, setStatsCache] = useState<Record<string, ExperimentStats>>({});

  async function loadStats(id: string) {
    if (statsCache[id]) return;
    setPhase('loading_stats');
    try {
      const s = await api.get(`/v1/experiments/${id}/stats`) as ExperimentStats;
      setStatsCache((prev) => ({ ...prev, [id]: s }));
    } catch (err) {
      setErrorMsg(err instanceof ApiError ? err.message : 'Errore nel caricamento statistiche');
    } finally {
      setPhase('idle');
    }
  }

  async function handleCreate(data: {
    name: string;
    variant_a_subject: string;
    variant_b_subject: string;
    split_pct: number;
  }) {
    setPhase('creating');
    setErrorMsg(null);
    try {
      await api.post('/v1/experiments', data);
      setShowCreate(false);
      router.refresh();
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError ? err.message : 'Errore nella creazione',
      );
    } finally {
      setPhase('idle');
    }
  }

  async function handleDeclareWinner(id: string, winner: 'a' | 'b') {
    if (
      !window.confirm(
        `Dichiarare la variante ${winner.toUpperCase()} come vincitrice? L'esperimento verrà terminato.`,
      )
    )
      return;
    setPhase('patching');
    setErrorMsg(null);
    try {
      await api.patch(`/v1/experiments/${id}`, { winner });
      router.refresh();
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError ? err.message : 'Errore nella dichiarazione',
      );
    } finally {
      setPhase('idle');
    }
  }

  async function handleEnd(id: string) {
    if (!window.confirm('Terminare l\'esperimento senza dichiarare un vincitore?'))
      return;
    setPhase('patching');
    setErrorMsg(null);
    try {
      await api.patch(`/v1/experiments/${id}`, { ended_at: new Date().toISOString() });
      router.refresh();
    } catch (err) {
      setErrorMsg(
        err instanceof ApiError ? err.message : 'Errore nella terminazione',
      );
    } finally {
      setPhase('idle');
    }
  }

  return (
    <div className="space-y-6">
      {/* Global error banner */}
      {errorMsg && (
        <div className="flex items-start gap-3 rounded-lg bg-error-container/40 px-4 py-3 text-sm text-on-error-container">
          <span aria-hidden className="mt-0.5">⚠</span>
          <p className="flex-1">{errorMsg}</p>
          <button
            onClick={() => setErrorMsg(null)}
            className="shrink-0 font-semibold underline hover:no-underline"
          >
            OK
          </button>
        </div>
      )}

      {/* AI generator — always visible at top level */}
      <div className="rounded-xl border border-primary/20 bg-primary-container/10 p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-sm font-semibold text-primary">
              ✨ Genera oggetti email con AI
            </p>
            <p className="mt-0.5 text-xs text-on-surface-variant">
              Claude genera varianti ottimizzate — assegna le migliori a
              Variante&nbsp;A e B per testarle.
            </p>
          </div>
          <button
            type="button"
            onClick={() => setShowTopAi((p) => !p)}
            disabled={phase !== 'idle'}
            className={cn(
              'shrink-0 rounded-lg px-4 py-2 text-xs font-semibold transition-colors',
              showTopAi
                ? 'bg-surface-container-high text-on-surface'
                : 'bg-primary text-on-primary hover:bg-primary/90',
              'disabled:cursor-not-allowed disabled:opacity-50',
            )}
          >
            {showTopAi ? 'Chiudi' : '✨ Apri generatore'}
          </button>
        </div>

        {showTopAi && (
          <div className="mt-4 border-t border-primary/10 pt-4">
            <AiVariantPicker
              onSelectVariant={(v, slot) => {
                setPrefill((prev) =>
                  slot === 'a' ? { ...prev, subjA: v.subject } : { ...prev, subjB: v.subject },
                );
                // auto-open the form so the user can review and submit
                setShowCreate(true);
              }}
            />
          </div>
        )}
      </div>

      {/* Create form toggle */}
      <div className="flex justify-end">
        <button
          onClick={() => {
            setShowCreate((p) => !p);
            if (showCreate) setPrefill({});
          }}
          disabled={phase !== 'idle'}
          className={cn(
            'rounded-lg px-4 py-2 text-sm font-semibold transition-colors',
            showCreate
              ? 'bg-surface-container-high text-on-surface'
              : 'bg-primary text-on-primary hover:bg-primary/90',
            'disabled:cursor-not-allowed disabled:opacity-50',
          )}
        >
          {showCreate ? 'Annulla' : '+ Nuovo esperimento'}
        </button>
      </div>

      {showCreate && (
        <CreateForm
          onSubmit={handleCreate}
          disabled={phase !== 'idle'}
          prefill={prefill}
        />
      )}

      {/* Experiments list */}
      {initialRows.length === 0 && !showCreate ? (
        <div className="rounded-lg bg-surface-container-low px-6 py-12 text-center">
          <p className="text-sm text-on-surface-variant">
            Nessun esperimento ancora. Crea il primo per testare due oggetti
            email e scoprire quale genera più aperture.
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {initialRows.map((exp) => (
            <ExperimentCard
              key={exp.id}
              exp={exp}
              stats={statsCache[exp.id] ?? null}
              onLoadStats={() => loadStats(exp.id)}
              onDeclareWinner={(w) => handleDeclareWinner(exp.id, w)}
              onEnd={() => handleEnd(exp.id)}
              busy={phase !== 'idle'}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create form
// ---------------------------------------------------------------------------

function CreateForm({
  onSubmit,
  disabled,
  prefill = {},
}: {
  onSubmit: (d: {
    name: string;
    variant_a_subject: string;
    variant_b_subject: string;
    split_pct: number;
  }) => void;
  disabled: boolean;
  prefill?: PrefillData;
}) {
  const [name, setName] = useState('');
  const [subjA, setSubjA] = useState(prefill.subjA ?? '');
  const [subjB, setSubjB] = useState(prefill.subjB ?? '');
  const [split, setSplit] = useState(50);

  // Sync if prefill changes (e.g. user picks another AI variant)
  const prevPrefill = useRef(prefill);
  useEffect(() => {
    if (prefill !== prevPrefill.current) {
      if (prefill.subjA !== undefined) setSubjA(prefill.subjA);
      if (prefill.subjB !== undefined) setSubjB(prefill.subjB);
      prevPrefill.current = prefill;
    }
  }, [prefill]);

  const valid = name.trim() && subjA.trim() && subjB.trim() && split >= 1 && split <= 99;

  return (
    <div className="rounded-xl border border-outline-variant/30 bg-surface-container-lowest p-5">
      <div className="mb-4 flex items-center justify-between">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Nuovo esperimento A/B
        </p>
        {(subjA || subjB) && (
          <span className="text-[10px] font-semibold text-primary bg-primary-container/30 rounded-full px-2 py-0.5">
            ✨ Oggetti pre-compilati dall&apos;AI
          </span>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="md:col-span-2">
          <label className="mb-1 block text-xs font-semibold text-on-surface">
            Nome esperimento
          </label>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="es. Test oggetto urgenza vs beneficio"
            maxLength={120}
            className="w-full rounded-lg bg-surface-container px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-variant/50 outline-none focus:ring-2 focus:ring-primary/40"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-semibold text-on-surface">
            Variante A — oggetto email
          </label>
          <input
            value={subjA}
            onChange={(e) => setSubjA(e.target.value)}
            placeholder="es. Risparmia €1.200/anno con il fotovoltaico"
            maxLength={300}
            className="w-full rounded-lg bg-surface-container px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-variant/50 outline-none focus:ring-2 focus:ring-primary/40"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-semibold text-on-surface">
            Variante B — oggetto email
          </label>
          <input
            value={subjB}
            onChange={(e) => setSubjB(e.target.value)}
            placeholder="es. Il tuo tetto può produrre energia — simulazione gratuita"
            maxLength={300}
            className="w-full rounded-lg bg-surface-container px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-variant/50 outline-none focus:ring-2 focus:ring-primary/40"
          />
        </div>
        <div>
          <label className="mb-1 block text-xs font-semibold text-on-surface">
            Split A/B (% inviati alla variante A)
          </label>
          <div className="flex items-center gap-3">
            <input
              type="range"
              min={1}
              max={99}
              value={split}
              onChange={(e) => setSplit(Number(e.target.value))}
              className="flex-1"
            />
            <span className="w-14 text-right text-sm font-semibold text-on-surface">
              {split}/{100 - split}
            </span>
          </div>
        </div>
        <div className="flex items-end justify-end">
          <button
            onClick={() =>
              valid &&
              onSubmit({
                name: name.trim(),
                variant_a_subject: subjA.trim(),
                variant_b_subject: subjB.trim(),
                split_pct: split,
              })
            }
            disabled={!valid || disabled}
            className={cn(
              'rounded-lg px-5 py-2.5 text-sm font-semibold transition-colors',
              'bg-primary text-on-primary hover:bg-primary/90',
              'disabled:cursor-not-allowed disabled:opacity-50',
            )}
          >
            Crea esperimento
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// AI Variant Picker
// ---------------------------------------------------------------------------

const TONES = [
  { value: 'professional', label: 'Professionale' },
  { value: 'urgent', label: 'Urgenza stagionale' },
  { value: 'friendly', label: 'Cordiale / vicino' },
  { value: 'roi_focused', label: 'ROI & numeri' },
] as const;

type Tone = (typeof TONES)[number]['value'];

function AiVariantPicker({
  onSelectVariant,
}: {
  onSelectVariant: (v: AiVariant, slot: 'a' | 'b') => void;
}) {
  const [segment, setSegment] = useState<'b2b' | 'b2c'>('b2c');
  const [tone, setTone] = useState<Tone>('professional');
  const [hint, setHint] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [variants, setVariants] = useState<AiVariant[]>([]);

  async function generate() {
    setLoading(true);
    setError(null);
    try {
      const res = await api.post<AiVariantsResponse>(
        '/v1/branding/generate-variants',
        {
          subject_type: segment,
          tone,
          count: 4,
          context_hint: hint.trim() || null,
        },
      );
      setVariants(res.variants);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mb-4 rounded-xl border border-primary/20 bg-primary-container/10 p-4">
      <p className="mb-3 text-xs font-semibold text-primary">
        ✨ Genera oggetti email con AI — poi assegnali alla variante A o B
      </p>

      <div className="grid gap-3 md:grid-cols-3">
        {/* Segment */}
        <div>
          <label className="mb-1 block text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Segmento
          </label>
          <div className="flex rounded-lg border border-outline-variant/40 bg-surface-container-lowest text-xs overflow-hidden">
            {(['b2c', 'b2b'] as const).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => setSegment(s)}
                className={cn(
                  'flex-1 px-3 py-1.5 font-semibold transition-colors',
                  segment === s
                    ? 'bg-primary text-on-primary'
                    : 'text-on-surface-variant hover:bg-surface-container',
                )}
              >
                {s.toUpperCase()}
              </button>
            ))}
          </div>
        </div>

        {/* Tone */}
        <div>
          <label className="mb-1 block text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Tono
          </label>
          <select
            value={tone}
            onChange={(e) => setTone(e.target.value as Tone)}
            className="w-full rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-3 py-1.5 text-xs text-on-surface focus:outline-none focus:ring-2 focus:ring-primary/40"
          >
            {TONES.map((t) => (
              <option key={t.value} value={t.value}>
                {t.label}
              </option>
            ))}
          </select>
        </div>

        {/* Hint */}
        <div>
          <label className="mb-1 block text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Contesto (opzionale)
          </label>
          <input
            type="text"
            value={hint}
            onChange={(e) => setHint(e.target.value)}
            placeholder="es. punta sull'estate e la bolletta alta"
            maxLength={200}
            className="w-full rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-3 py-1.5 text-xs text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
        </div>
      </div>

      <button
        type="button"
        disabled={loading}
        onClick={generate}
        className="mt-3 rounded-lg bg-primary px-4 py-1.5 text-xs font-semibold text-on-primary transition-opacity disabled:opacity-50 hover:opacity-90"
      >
        {loading ? 'Generazione in corso…' : '✨ Genera varianti'}
      </button>

      {error && (
        <p className="mt-2 text-xs text-error">{error}</p>
      )}

      {variants.length > 0 && (
        <div className="mt-4 space-y-2">
          <p className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Varianti generate — clicca per assegnare
          </p>
          {variants.map((v, i) => (
            <div
              key={i}
              className="rounded-lg border border-outline-variant/20 bg-surface-container-lowest p-3"
            >
              <p className="text-sm font-semibold text-on-surface leading-snug">
                {v.subject}
              </p>
              {v.preheader && (
                <p className="mt-0.5 text-xs text-on-surface-variant">
                  {v.preheader}
                </p>
              )}
              {v.rationale && (
                <p className="mt-1 text-[11px] italic text-on-surface-variant">
                  {v.rationale}
                </p>
              )}
              <div className="mt-2 flex gap-2">
                <button
                  type="button"
                  onClick={() => onSelectVariant(v, 'a')}
                  className="rounded-full bg-primary-container/50 px-2.5 py-0.5 text-[10px] font-bold text-on-primary-container hover:bg-primary-container transition-colors"
                >
                  Usa come A
                </button>
                <button
                  type="button"
                  onClick={() => onSelectVariant(v, 'b')}
                  className="rounded-full bg-surface-container-high px-2.5 py-0.5 text-[10px] font-semibold text-on-surface hover:bg-surface-container transition-colors"
                >
                  Usa come B
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Experiment card
// ---------------------------------------------------------------------------

function ExperimentCard({
  exp,
  stats,
  onLoadStats,
  onDeclareWinner,
  onEnd,
  busy,
}: {
  exp: ExperimentRow;
  stats: ExperimentStats | null;
  onLoadStats: () => void;
  onDeclareWinner: (w: 'a' | 'b') => void;
  onEnd: () => void;
  busy: boolean;
}) {
  const isRunning = !exp.ended_at;
  const [showStats, setShowStats] = useState(false);

  function toggleStats() {
    if (!showStats) onLoadStats();
    setShowStats((p) => !p);
  }

  return (
    <div className="rounded-xl border border-outline-variant/30 bg-surface-container-lowest p-5">
      {/* Header */}
      <div className="flex flex-wrap items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <p className="font-semibold text-on-surface">{exp.name}</p>
            <StatusBadge exp={exp} />
          </div>
          <p className="mt-0.5 text-xs text-on-surface-variant">
            Avviato {relativeTime(exp.started_at)}
            {exp.ended_at && ` · Terminato ${relativeTime(exp.ended_at)}`}
            {' · '}Split {exp.split_pct}/{100 - exp.split_pct}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={toggleStats}
            className="rounded-lg border border-outline-variant/40 bg-surface-container px-3 py-1.5 text-xs font-semibold text-on-surface hover:bg-surface-container-high transition-colors"
          >
            {showStats ? 'Nascondi' : 'Statistiche'}
          </button>
          {isRunning && !exp.winner && (
            <button
              onClick={onEnd}
              disabled={busy}
              className="rounded-lg border border-outline-variant/40 bg-surface-container px-3 py-1.5 text-xs font-semibold text-on-surface hover:bg-surface-container-high transition-colors disabled:opacity-50"
            >
              Termina
            </button>
          )}
        </div>
      </div>

      {/* Variants */}
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <VariantRow
          label="A"
          subject={exp.variant_a_subject}
          isWinner={exp.winner === 'a'}
        />
        <VariantRow
          label="B"
          subject={exp.variant_b_subject}
          isWinner={exp.winner === 'b'}
        />
      </div>

      {/* Stats panel */}
      {showStats && (
        <StatsPanel
          stats={stats}
          isRunning={isRunning}
          winner={exp.winner}
          onDeclareWinner={onDeclareWinner}
          busy={busy}
        />
      )}
    </div>
  );
}

function VariantRow({
  label,
  subject,
  isWinner,
}: {
  label: string;
  subject: string;
  isWinner: boolean;
}) {
  return (
    <div
      className={cn(
        'rounded-lg px-4 py-3',
        isWinner
          ? 'bg-primary-container/30 ring-1 ring-primary/30'
          : 'bg-surface-container',
      )}
    >
      <div className="mb-1 flex items-center gap-2">
        <span className="flex h-5 w-5 items-center justify-center rounded-full bg-primary text-[10px] font-bold text-on-primary">
          {label}
        </span>
        {isWinner && (
          <span className="text-[11px] font-semibold text-primary">
            🏆 Vincitore
          </span>
        )}
      </div>
      <p className="text-sm text-on-surface leading-snug">{subject}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Stats panel (Bayesian)
// ---------------------------------------------------------------------------

function StatsPanel({
  stats,
  isRunning,
  winner,
  onDeclareWinner,
  busy,
}: {
  stats: ExperimentStats | null;
  isRunning: boolean;
  winner: 'a' | 'b' | null;
  onDeclareWinner: (w: 'a' | 'b') => void;
  busy: boolean;
}) {
  if (!stats) {
    return (
      <div className="mt-4 rounded-lg bg-surface-container-low px-4 py-6 text-center">
        <p className="text-xs text-on-surface-variant">Caricamento statistiche…</p>
      </div>
    );
  }

  const rows: Array<{
    label: string;
    a: string;
    b: string;
    prob: number;
    verdict: ExperimentVerdict;
  }> = [
    {
      label: 'Invii',
      a: String(stats.a.sends),
      b: String(stats.b.sends),
      prob: stats.prob_a_wins_open,
      verdict: 'in_corso',
    },
    {
      label: 'Aperture',
      a: `${stats.a.opens} (${pct(stats.a.open_rate)})`,
      b: `${stats.b.opens} (${pct(stats.b.open_rate)})`,
      prob: stats.prob_a_wins_open,
      verdict: stats.verdict_open,
    },
    {
      label: 'Click',
      a: `${stats.a.clicks} (${pct(stats.a.click_rate)})`,
      b: `${stats.b.clicks} (${pct(stats.b.click_rate)})`,
      prob: stats.prob_a_wins_click,
      verdict: stats.verdict_click,
    },
  ];

  return (
    <div className="mt-4 space-y-4">
      <div className="overflow-x-auto rounded-lg border border-outline-variant/20">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant bg-surface-container">
              <th className="px-4 py-2">Metrica</th>
              <th className="px-4 py-2">A</th>
              <th className="px-4 py-2">B</th>
              <th className="px-4 py-2">P(A vince)</th>
              <th className="px-4 py-2">Verdetto</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr
                key={row.label}
                className={cn(
                  'text-xs',
                  i !== 0 && 'border-t border-outline-variant/10',
                )}
              >
                <td className="px-4 py-2.5 font-semibold text-on-surface">
                  {row.label}
                </td>
                <td className="px-4 py-2.5 text-on-surface-variant">{row.a}</td>
                <td className="px-4 py-2.5 text-on-surface-variant">{row.b}</td>
                <td className="px-4 py-2.5">
                  {i > 0 ? (
                    <BayesBar prob={row.prob} />
                  ) : (
                    <span className="text-on-surface-variant">—</span>
                  )}
                </td>
                <td className="px-4 py-2.5">
                  {i > 0 ? <VerdictBadge verdict={row.verdict} /> : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {!stats.min_sample_met && (
        <p className="text-xs text-on-surface-variant italic">
          Servono almeno 20 invii per variante per il calcolo Bayesiano.
          Attualmente: A={stats.a.sends}, B={stats.b.sends}.
        </p>
      )}

      {/* Declare winner CTA — only when running, no winner yet, min sample met */}
      {isRunning && !winner && stats.min_sample_met && (
        <div className="flex gap-3">
          <button
            onClick={() => onDeclareWinner('a')}
            disabled={busy}
            className="rounded-lg bg-surface-container px-4 py-2 text-xs font-semibold text-on-surface hover:bg-surface-container-high transition-colors disabled:opacity-50"
          >
            Dichiara A vincitore
          </button>
          <button
            onClick={() => onDeclareWinner('b')}
            disabled={busy}
            className="rounded-lg bg-surface-container px-4 py-2 text-xs font-semibold text-on-surface hover:bg-surface-container-high transition-colors disabled:opacity-50"
          >
            Dichiara B vincitore
          </button>
        </div>
      )}
    </div>
  );
}

function BayesBar({ prob }: { prob: number }) {
  const pctVal = Math.round(prob * 100);
  const color =
    prob >= 0.95
      ? 'bg-primary'
      : prob <= 0.05
        ? 'bg-error'
        : 'bg-secondary-container';

  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 overflow-hidden rounded-full bg-surface-container-high">
        <div
          className={cn('h-full rounded-full transition-all', color)}
          style={{ width: `${pctVal}%` }}
        />
      </div>
      <span className="text-on-surface-variant">{pctVal}%</span>
    </div>
  );
}

const VERDICT_CONFIG: Record<
  ExperimentVerdict,
  { label: string; cls: string }
> = {
  a_wins: { label: 'A vince (95%)', cls: 'bg-primary-container/60 text-on-primary-container' },
  b_wins: { label: 'B vince (95%)', cls: 'bg-secondary-container/60 text-on-secondary-container' },
  in_corso: { label: 'In corso', cls: 'bg-surface-container-high text-on-surface-variant' },
  no_data: { label: 'Troppo pochi dati', cls: 'bg-surface-container-high text-on-surface-variant' },
};

function VerdictBadge({ verdict }: { verdict: ExperimentVerdict }) {
  const { label, cls } = VERDICT_CONFIG[verdict];
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold',
        cls,
      )}
    >
      {label}
    </span>
  );
}

function StatusBadge({ exp }: { exp: ExperimentRow }) {
  if (exp.winner) {
    return (
      <span className="inline-flex items-center rounded-full bg-primary-container/50 px-2 py-0.5 text-[10px] font-semibold text-on-primary-container">
        Concluso · Vincitore: {exp.winner.toUpperCase()}
      </span>
    );
  }
  if (exp.ended_at) {
    return (
      <span className="inline-flex items-center rounded-full bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold text-on-surface-variant">
        Terminato
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-tertiary-container/50 px-2 py-0.5 text-[10px] font-semibold text-on-tertiary-container">
      <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-tertiary" />
      In corso
    </span>
  );
}

function pct(rate: number): string {
  return `${Math.round(rate * 100)}%`;
}
