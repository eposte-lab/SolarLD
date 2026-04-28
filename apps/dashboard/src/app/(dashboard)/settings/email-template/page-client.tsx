'use client';

/**
 * EmailTemplatePageClient — Sprint 9 Fase C.5.
 *
 * Client component that owns the tab state and fetches initial data
 * from the API on mount. Renders two tabs:
 *
 *   "Template"  — 3 template family cards + custom HTML uploader
 *   "A/B Test"  — ClusterAbPanel
 */

import { useCallback, useEffect, useState } from 'react';
import { TemplateUploader } from '@/components/email-template/template-uploader';
import { TemplatePreviewIframe } from '@/components/email-template/template-preview-iframe';
import { ClusterAbPanel } from '@/components/email-template/cluster-ab-panel';
import {
  getEmailTemplateInfo,
  listActiveClusters,
  getCustomTemplatePreviewUrl,
  type TemplateInfo,
  type ClusterAB,
} from '@/lib/data/cluster-ab';
import { API_URL } from '@/lib/api-client';

type Tab = 'template' | 'ab';

type TemplateFamily = 'premium' | 'legacy_visual' | 'plain_conversational' | 'custom';

const TEMPLATE_CARDS: {
  id: TemplateFamily;
  title: string;
  description: string;
  previewPath: string;
}[] = [
  {
    id: 'premium',
    title: 'Premium SolarLead',
    description:
      'Template 600px single-column, mobile-first con blocco ROI, GIF rendering da CDN, ' +
      'footer GDPR e 4 variabili A/B dinamiche. Default per tutti i nuovi account.',
    previewPath: '/v1/branding/email-preview?template=b2b&style=classic',
  },
  {
    id: 'legacy_visual',
    title: 'Visual Preventivo',
    description:
      'Il template ricco con hero image, card dati tetto e grafica pesante. ' +
      'Ancora supportato per tenant che lo usano storicamente.',
    previewPath: '/v1/branding/email-preview?template=b2b&style=classic',
  },
  {
    id: 'plain_conversational',
    title: 'Conversazionale',
    description:
      '60-80 parole, testo semplice, sembra una vera email umana. Ideale per cold outreach ' +
      'B2B su domini nuovi dove l\'HTML pesante penalizza la deliverability.',
    previewPath: '/v1/branding/email-preview?template=b2b',
  },
];

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-4 py-2 text-sm font-medium rounded-xl transition-colors ${
        active
          ? 'bg-primary text-white'
          : 'text-on-surface-variant hover:bg-surface-variant/40'
      }`}
    >
      {children}
    </button>
  );
}

function TemplateFamilyCard({
  card,
  active,
  previewUrl,
  onSelect,
}: {
  card: (typeof TEMPLATE_CARDS)[number];
  active: boolean;
  previewUrl: string;
  onSelect: () => void;
}) {
  const [showPreview, setShowPreview] = useState(false);

  return (
    <div
      className={`rounded-2xl border-2 p-5 space-y-3 transition-all ${
        active ? 'border-primary bg-primary/5' : 'border-outline/30 bg-surface'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="font-semibold text-base">{card.title}</h3>
          <p className="text-xs text-on-surface-variant mt-1">{card.description}</p>
        </div>
        {active && (
          <span className="shrink-0 text-xs bg-primary text-white px-2 py-0.5 rounded-full">
            Attivo
          </span>
        )}
      </div>

      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => setShowPreview((s) => !s)}
          className="text-xs text-primary underline"
        >
          {showPreview ? 'Nascondi anteprima' : 'Mostra anteprima'}
        </button>
        {!active && (
          <button
            type="button"
            onClick={onSelect}
            className="text-xs bg-primary text-white px-3 py-1 rounded-lg"
          >
            Attiva
          </button>
        )}
      </div>

      {showPreview && (
        <TemplatePreviewIframe src={`${API_URL}${previewUrl}`} height={480} />
      )}
    </div>
  );
}

export function EmailTemplatePageClient() {
  const [tab, setTab] = useState<Tab>('template');
  const [templateInfo, setTemplateInfo] = useState<TemplateInfo | null>(null);
  const [clusters, setClusters] = useState<ClusterAB[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeFamily, setActiveFamily] = useState<TemplateFamily>('premium');
  const [customPreviewUrl, setCustomPreviewUrl] = useState<string>('');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [info, clustersRes] = await Promise.all([
        getEmailTemplateInfo().catch(() => null),
        listActiveClusters().catch(() => ({ clusters: [], total: 0 })),
      ]);
      setTemplateInfo(info);
      setClusters(clustersRes.clusters);

      // Determine active family from template info.
      if (info?.active) {
        setActiveFamily('custom');
      }
      // TODO: read email_template_family from tenant settings when endpoint is ready.
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    // Get custom preview URL (needs auth).
    getCustomTemplatePreviewUrl().then(setCustomPreviewUrl).catch(() => {});
  }, [load]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-on-surface-variant">
        <span className="animate-spin">⟳</span> Caricamento…
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Tab bar */}
      <div className="flex gap-2 rounded-2xl bg-surface-variant/20 p-1 w-fit">
        <TabButton active={tab === 'template'} onClick={() => setTab('template')}>
          Template
        </TabButton>
        <TabButton active={tab === 'ab'} onClick={() => setTab('ab')}>
          A/B Test
          {clusters.length > 0 && (
            <span className="ml-1.5 inline-flex h-4 w-4 items-center justify-center rounded-full bg-primary/20 text-[10px] font-bold text-primary">
              {clusters.length}
            </span>
          )}
        </TabButton>
      </div>

      {/* ── Template tab ───────────────────────────────────────────── */}
      {tab === 'template' && (
        <div className="space-y-6">
          {/* Built-in family cards */}
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {TEMPLATE_CARDS.map((card) => (
              <TemplateFamilyCard
                key={card.id}
                card={card}
                active={activeFamily === card.id}
                previewUrl={card.previewPath}
                onSelect={() => {
                  setActiveFamily(card.id);
                  // TODO: PATCH /v1/tenants/email-template-family when endpoint is ready.
                }}
              />
            ))}
          </div>

          {/* Custom HTML upload */}
          <div className="rounded-2xl border-2 border-outline/30 bg-surface p-5 space-y-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="font-semibold text-base">HTML personalizzato</h3>
                <p className="text-xs text-on-surface-variant mt-1">
                  Carica il tuo template Jinja2. Deve includere le variabili GDPR obbligatorie.{' '}
                  {templateInfo?.active && (
                    <span className="text-primary font-medium">Attualmente attivo.</span>
                  )}
                </p>
              </div>
              {activeFamily === 'custom' && (
                <span className="shrink-0 text-xs bg-primary text-white px-2 py-0.5 rounded-full">
                  Attivo
                </span>
              )}
            </div>

            {/* Upload metadata */}
            {templateInfo?.uploaded_at && (
              <p className="text-xs text-on-surface-variant">
                Ultimo upload:{' '}
                {new Date(templateInfo.uploaded_at).toLocaleString('it-IT')}
              </p>
            )}

            {/* Uploader */}
            <TemplateUploader templateInfo={templateInfo} onSaved={load} />

            {/* Preview of custom template */}
            {templateInfo?.active && customPreviewUrl && (
              <div className="space-y-2">
                <p className="text-xs font-medium">Anteprima template personalizzato:</p>
                <TemplatePreviewIframe src={customPreviewUrl} height={520} />
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── A/B Test tab ───────────────────────────────────────────── */}
      {tab === 'ab' && (
        <div className="space-y-4">
          <div className="rounded-2xl border bg-surface-variant/20 p-4">
            <h3 className="font-semibold text-sm mb-1">Come funziona il motore A/B per cluster</h3>
            <p className="text-xs text-on-surface-variant">
              Ogni cluster (es. <code className="font-mono">ateco41_m_ceo</code>) ha la propria
              coppia di varianti A/B generata da Claude Haiku. L&apos;assegnazione è deterministica:{' '}
              <code className="font-mono">hash(lead_id) % 2</code>. Ogni notte alle 03:30 UTC il
              motore valuta i risultati con chi-square 2×2 (Yates, df=1). Quando{' '}
              <em>p &lt; 0.05</em> e almeno 100 invii per variante, il vincitore viene promosso e
              viene generato un nuovo round con il vincitore come baseline.
            </p>
          </div>

          <ClusterAbPanel initialClusters={clusters} />
        </div>
      )}
    </div>
  );
}
