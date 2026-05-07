'use client';

/**
 * ClusterAbPanel — Sprint 9 Fase B.6 / C.5.
 *
 * Shows all active cluster A/B test pairs with:
 *   - 4-stat strip: sent A / sent B / reply rate A / reply rate B
 *   - Side-by-side copy preview (4 fields per variant)
 *   - Bayesian P(A wins) badge
 *   - Manual promote / regenerate buttons
 */

import { useCallback, useState } from 'react';
import {
  listActiveClusters,
  promoteVariant,
  regenerateCluster,
  unlockConvergedCluster,
  type ClusterAB,
  type VariantCopy,
} from '@/lib/data/cluster-ab';

interface ClusterAbPanelProps {
  initialClusters: ClusterAB[];
}

function StatBadge({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border bg-surface-variant/20 px-3 py-2 text-center min-w-[80px]">
      <p className="text-xs text-on-surface-variant">{label}</p>
      <p className="font-semibold text-sm">{value}</p>
    </div>
  );
}

function VariantCard({ variant, label }: { variant: VariantCopy; label: string }) {
  return (
    <div className="flex-1 rounded-xl border bg-surface p-4 space-y-2 min-w-0">
      <div className="flex items-center justify-between">
        <span className="text-xs font-bold uppercase tracking-wide text-on-surface-variant">
          Variante {label}
        </span>
        <span
          className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            variant.status === 'active'
              ? 'bg-primary/10 text-primary'
              : 'bg-surface-variant text-on-surface-variant'
          }`}
        >
          {variant.status}
        </span>
      </div>
      <div className="space-y-1 text-xs">
        <p>
          <span className="text-on-surface-variant">Oggetto: </span>
          <span className="font-medium">{variant.copy_subject}</span>
        </p>
        <p>
          <span className="text-on-surface-variant">Apertura: </span>
          {variant.copy_opening_line}
        </p>
        <p>
          <span className="text-on-surface-variant">Proposizione: </span>
          {variant.copy_proposition_line}
        </p>
        <p>
          <span className="text-on-surface-variant">CTA: </span>
          <span className="font-medium italic">{variant.cta_primary_label}</span>
        </p>
      </div>
    </div>
  );
}

function ConvergedClusterCard({
  cluster,
  onAction,
}: {
  cluster: ClusterAB;
  onAction: () => void;
}) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const champion = cluster.variants[0];

  const handleUnlock = useCallback(async () => {
    if (
      !confirm(
        `Sfidare il vincitore di "${cluster.cluster_signature}"? ` +
          'Verrà generata una nuova variante B sfidante e il test ripartirà.',
      )
    )
      return;
    setLoading(true);
    setError('');
    try {
      await unlockConvergedCluster(cluster.cluster_signature);
      onAction();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Errore unlock');
    } finally {
      setLoading(false);
    }
  }, [cluster.cluster_signature, onAction]);

  const convergedDate = cluster.converged_at
    ? new Date(cluster.converged_at).toLocaleDateString('it-IT', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
      })
    : null;

  return (
    <div className="rounded-2xl border border-primary/30 bg-primary/5 shadow-sm overflow-hidden">
      <div className="px-5 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3 flex-wrap">
          <span className="font-mono text-sm font-semibold">
            {cluster.cluster_signature}
          </span>
          <span className="text-xs bg-primary text-on-primary px-2.5 py-1 rounded-full font-semibold inline-flex items-center gap-1">
            🏆 Stabile
          </span>
          {convergedDate && (
            <span className="text-xs text-on-surface-variant">
              vincitore consolidato il {convergedDate}
            </span>
          )}
        </div>
      </div>

      {champion && (
        <div className="px-5 pb-4 space-y-3">
          {error && (
            <p className="text-sm text-red-600 rounded-lg bg-red-50 px-3 py-2">
              {error}
            </p>
          )}
          <div className="rounded-xl border bg-surface p-4 space-y-2">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold uppercase tracking-wide text-primary">
                Champion · Variante {champion.variant_label}
              </span>
              <span className="text-xs text-on-surface-variant">
                Round {champion.round_number} · {champion.sent_count} invii ·{' '}
                {champion.replied_count} risposte
              </span>
            </div>
            <div className="space-y-1 text-xs">
              <p>
                <span className="text-on-surface-variant">Oggetto: </span>
                <span className="font-medium">{champion.copy_subject}</span>
              </p>
              <p>
                <span className="text-on-surface-variant">Apertura: </span>
                {champion.copy_opening_line}
              </p>
              <p>
                <span className="text-on-surface-variant">Proposizione: </span>
                {champion.copy_proposition_line}
              </p>
              <p>
                <span className="text-on-surface-variant">CTA: </span>
                <span className="font-medium italic">
                  {champion.cta_primary_label}
                </span>
              </p>
            </div>
          </div>
          <p className="text-xs text-on-surface-variant">
            Questa variante riceve il 100% del traffico. Sarà sfidata
            automaticamente fra 90 giorni per controllo deriva, oppure
            puoi forzare un nuovo test ora.
          </p>
          <button
            type="button"
            onClick={handleUnlock}
            disabled={loading}
            className="text-sm rounded-xl border border-primary px-4 py-1.5 text-primary hover:bg-primary/10 transition-colors disabled:opacity-50"
          >
            {loading ? 'Generazione sfidante…' : '🔄 Sfida il vincitore'}
          </button>
        </div>
      )}
    </div>
  );
}

function ClusterCard({ cluster, onAction }: { cluster: ClusterAB; onAction: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [loading, setLoading] = useState<string | null>(null);
  const [error, setError] = useState('');

  const va = cluster.variants.find((v) => v.variant_label === 'A');
  const vb = cluster.variants.find((v) => v.variant_label === 'B');

  const fmtRate = (r: number | null) =>
    r != null ? `${(r * 100).toFixed(1)}%` : '—';

  const handlePromote = useCallback(
    async (variantId: string, label: string) => {
      if (!confirm(`Promuovere la variante ${label} come vincitrice? Verranno generati nuovi A/B.`))
        return;
      setLoading(`promote-${variantId}`);
      setError('');
      try {
        await promoteVariant(variantId);
        onAction();
      } catch (err: unknown) {
        setError((err instanceof Error ? err.message : 'Errore promozione'));
      } finally {
        setLoading(null);
      }
    },
    [onAction],
  );

  const handleRegenerate = useCallback(async () => {
    if (!confirm(`Generare un nuovo round A/B da zero per "${cluster.cluster_signature}"?`)) return;
    setLoading('regenerate');
    setError('');
    try {
      await regenerateCluster(cluster.cluster_signature);
      onAction();
    } catch (err: unknown) {
      setError((err instanceof Error ? err.message : 'Errore rigenerazione'));
    } finally {
      setLoading(null);
    }
  }, [cluster.cluster_signature, onAction]);

  return (
    <div className="rounded-2xl border bg-surface shadow-sm overflow-hidden">
      {/* Header */}
      <button
        type="button"
        onClick={() => setExpanded((e) => !e)}
        className="w-full flex items-center justify-between px-5 py-4 hover:bg-surface-variant/10 transition-colors text-left"
      >
        <div className="flex items-center gap-3">
          <span className="font-mono text-sm font-semibold">{cluster.cluster_signature}</span>
          <span className="text-xs bg-primary/10 text-primary px-2 py-0.5 rounded-full">
            Round {cluster.round_number}
          </span>
          {cluster.prob_a_wins != null && (
            <span className="text-xs text-on-surface-variant">
              P(A vince) {(cluster.prob_a_wins * 100).toFixed(0)}%
            </span>
          )}
        </div>
        <span className="text-on-surface-variant text-lg">{expanded ? '▲' : '▼'}</span>
      </button>

      {/* Stats strip — always visible */}
      {va && vb && (
        <div className="flex flex-wrap gap-2 px-5 pb-3">
          <StatBadge label="Inviati A" value={va.sent_count} />
          <StatBadge label="Inviati B" value={vb.sent_count} />
          <StatBadge label="Reply A" value={fmtRate(va.reply_rate)} />
          <StatBadge label="Reply B" value={fmtRate(vb.reply_rate)} />
        </div>
      )}

      {/* Expanded: copy side-by-side + actions */}
      {expanded && (
        <div className="px-5 pb-5 space-y-4 border-t pt-4">
          {error && (
            <p className="text-sm text-red-600 rounded-lg bg-red-50 px-3 py-2">{error}</p>
          )}

          {/* Copy preview */}
          <div className="flex gap-3">
            {va && <VariantCard variant={va} label="A" />}
            {vb && <VariantCard variant={vb} label="B" />}
          </div>

          {/* Actions */}
          <div className="flex flex-wrap gap-2">
            {va && (
              <button
                type="button"
                onClick={() => handlePromote(va.id, 'A')}
                disabled={!!loading}
                className="text-sm rounded-xl border border-primary px-4 py-1.5
                           text-primary hover:bg-primary/10 transition-colors disabled:opacity-50"
              >
                {loading === `promote-${va.id}` ? 'Promozione…' : 'Promuovi A'}
              </button>
            )}
            {vb && (
              <button
                type="button"
                onClick={() => handlePromote(vb.id, 'B')}
                disabled={!!loading}
                className="text-sm rounded-xl border border-primary px-4 py-1.5
                           text-primary hover:bg-primary/10 transition-colors disabled:opacity-50"
              >
                {loading === `promote-${vb.id}` ? 'Promozione…' : 'Promuovi B'}
              </button>
            )}
            <button
              type="button"
              onClick={handleRegenerate}
              disabled={!!loading}
              className="text-sm rounded-xl border border-outline/40 px-4 py-1.5
                         hover:bg-surface-variant/30 transition-colors disabled:opacity-50 ml-auto"
            >
              {loading === 'regenerate' ? 'Rigenerazione…' : 'Genera nuovo round'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function ClusterAbPanel({ initialClusters }: ClusterAbPanelProps) {
  const [clusters, setClusters] = useState<ClusterAB[]>(initialClusters);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      const res = await listActiveClusters();
      setClusters(res.clusters);
    } catch {
      // keep current state
    } finally {
      setRefreshing(false);
    }
  }, []);

  if (clusters.length === 0) {
    return (
      <div className="rounded-2xl border bg-surface-variant/20 p-8 text-center">
        <p className="text-sm text-on-surface-variant">
          Nessun cluster A/B attivo. Invia le prime email perché il motore generi le varianti.
        </p>
      </div>
    );
  }

  // Stable winners on top so the operator sees "what works" first;
  // active tests below for the still-running experiments.
  const stable = clusters.filter((c) => c.converged_at);
  const active = clusters.filter((c) => !c.converged_at);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-on-surface-variant">
          {active.length} test attiv{active.length === 1 ? 'o' : 'i'}
          {stable.length > 0
            ? ` · ${stable.length} stabil${stable.length === 1 ? 'e' : 'i'}`
            : ''}{' '}
          — valutazione automatica ogni notte
        </p>
        <button
          type="button"
          onClick={refresh}
          disabled={refreshing}
          className="text-xs text-primary underline disabled:opacity-50"
        >
          {refreshing ? 'Aggiornamento…' : 'Aggiorna'}
        </button>
      </div>

      {stable.map((c) => (
        <ConvergedClusterCard
          key={c.cluster_signature}
          cluster={c}
          onAction={refresh}
        />
      ))}
      {active.map((c) => (
        <ClusterCard key={c.cluster_signature} cluster={c} onAction={refresh} />
      ))}
    </div>
  );
}
