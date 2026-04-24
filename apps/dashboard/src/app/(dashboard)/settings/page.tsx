/**
 * Settings — overview page linking into each focused settings surface.
 *
 * Hunter configuration lives entirely in the five modules
 * (`/settings/modules/{sorgente,tecnico,economico,outreach,crm}`).
 * This page is the hub: reputation + integrations + plan + module
 * shortcuts. Everything Hunter-related clicks through to the module
 * editors — we don't try to summarize module config here because the
 * shape changes per-module and the module pages already own that UI.
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { BentoCard } from '@/components/ui/bento-card';
import { cn } from '@/lib/utils';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { getModulesForTenant } from '@/lib/data/modules.server';
import { getLatestDomainReputation } from '@/lib/data/reputation';
import {
  TIER_LABEL,
  TIER_ORDER,
  canTenantUse,
  resolveTierSnapshot,
  type CapabilityKey,
} from '@/lib/data/tier';
import type { ModuleKey, TenantModule } from '@/types/modules';
import type {
  DomainReputationRow,
  TenantRow,
  TenantTier,
} from '@/types/db';
import { GradientButton } from '@/components/ui/gradient-button';

const MODULE_META: Record<
  ModuleKey,
  { title: string; blurb: string; icon: string }
> = {
  sorgente: {
    title: 'Sorgente',
    blurb: 'ATECO, dimensioni aziendali, geografia. Guida il Livello 1 del funnel.',
    icon: '🧭',
  },
  tecnico: {
    title: 'Tecnico',
    blurb: 'Soglie tetto (kWp, ombreggiamento, esposizione) per il Solar gate.',
    icon: '⚙️',
  },
  economico: {
    title: 'Economico',
    blurb: 'Ticket medio, budget scan mensile, ROI target.',
    icon: '💶',
  },
  outreach: {
    title: 'Outreach',
    blurb: 'Canali attivi (email, postale, WhatsApp), tono di voce, CTA.',
    icon: '✉️',
  },
  crm: {
    title: 'CRM',
    blurb: 'Webhook in uscita, label pipeline, criteri di qualifica.',
    icon: '🔁',
  },
};

const MODULE_ORDER: readonly ModuleKey[] = [
  'sorgente',
  'tecnico',
  'economico',
  'outreach',
  'crm',
] as const;

export default async function SettingsPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const [modules, reputation] = await Promise.all([
    getModulesForTenant(ctx.tenant.id),
    getLatestDomainReputation(),
  ]);

  const domainSwitched =
    reputation !== null &&
    ctx.tenant.email_from_domain !== null &&
    reputation.email_from_domain !== (ctx.tenant.email_from_domain ?? '').toLowerCase();

  return (
    <div className="space-y-8">
      <Header tenantName={ctx.tenant.business_name} />

      {reputation && (reputation.alarm_bounce || reputation.alarm_complaint) && !domainSwitched && (
        <ReputationAlarmBanner reputation={reputation} />
      )}

      <ReputationCard
        reputation={reputation}
        tenant={ctx.tenant}
        domainSwitched={domainSwitched}
      />

      <ModulesCard modules={modules} />

      <IntegrationsCard tenant={ctx.tenant} />

      <PlanCard tenant={ctx.tenant} />

      {ctx.role === 'super_admin' && <DevToolsCard />}
    </div>
  );
}

// ---------------------------------------------------------------------------

function Header({ tenantName }: { tenantName: string }) {
  return (
    <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
      <div>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Impostazioni · {tenantName}
        </p>
        <h1 className="mt-1 font-headline text-4xl font-bold tracking-tighter text-on-surface md:text-5xl">
          Configurazione
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-on-surface-variant">
          Ogni area di Hunter vive in un modulo dedicato. Apri un modulo per
          modificarne le soglie — la modifica è immediata, nessun wizard da
          ripetere.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------

function ModulesCard({ modules }: { modules: TenantModule[] }) {
  const byKey = new Map(modules.map((m) => [m.module_key, m]));

  return (
    <BentoCard span="full">
      <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
        Moduli Hunter
      </p>
      <h2 className="mt-1 font-headline text-2xl font-bold tracking-tighter">
        Cinque aree, cinque pagine
      </h2>
      <p className="mt-1 max-w-2xl text-sm text-on-surface-variant">
        Ogni modulo controlla un pezzo del funnel. Puoi disattivare un modulo
        per escluderlo dalle scansioni senza perdere la configurazione.
      </p>

      <div className="mt-5 grid gap-3 md:grid-cols-2">
        {MODULE_ORDER.map((key) => {
          const row = byKey.get(key);
          const meta = MODULE_META[key];
          const configured = (row?.version ?? 0) >= 1;
          const active = row?.active ?? true;
          return (
            <Link
              key={key}
              href={{ pathname: `/settings/modules/${key}` }}
              className={cn(
                'group flex items-start justify-between gap-4 rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-4 py-4 transition-colors',
                'hover:border-primary/60 hover:bg-surface-container-low',
              )}
            >
              <div className="flex items-start gap-3">
                <span aria-hidden className="mt-0.5 text-xl leading-none">
                  {meta.icon}
                </span>
                <div>
                  <p className="font-semibold text-on-surface">{meta.title}</p>
                  <p className="mt-1 text-xs text-on-surface-variant">
                    {meta.blurb}
                  </p>
                </div>
              </div>
              <div className="flex flex-col items-end gap-1">
                <ModuleStatusChip configured={configured} active={active} />
                <span className="text-on-surface-variant group-hover:text-primary">→</span>
              </div>
            </Link>
          );
        })}
      </div>
    </BentoCard>
  );
}

function ModuleStatusChip({
  configured,
  active,
}: {
  configured: boolean;
  active: boolean;
}) {
  if (!configured) {
    return (
      <span className="rounded-full border border-outline-variant/60 bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
        Default
      </span>
    );
  }
  if (!active) {
    return (
      <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
        Disattivato
      </span>
    );
  }
  return (
    <span className="rounded-full bg-primary-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-primary-container">
      Attivo
    </span>
  );
}

// ---------------------------------------------------------------------------
// Integrations → subpages (CRM webhooks for now; room to grow)
// ---------------------------------------------------------------------------

function IntegrationsCard({ tenant }: { tenant: TenantRow }) {
  const crmAllowed = canTenantUse(tenant, 'crm_outbound_webhooks');
  return (
    <BentoCard span="full">
      <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
        Integrazioni
      </p>
      <h2 className="mt-1 font-headline text-2xl font-bold tracking-tighter">
        Connetti SolarLead ai tuoi strumenti
      </h2>
      <p className="mt-1 max-w-xl text-sm text-on-surface-variant">
        Gli eventi del ciclo di vita del lead possono essere spediti in uscita
        al tuo CRM così non devi sincronizzare a mano.
      </p>

      <div className="mt-5 grid gap-3 md:grid-cols-2">
        {/* ── Inbox mittenti (multi-inbox) ── */}
        <Link
          href={'/settings/inboxes'}
          className={cn(
            'group flex items-start justify-between gap-4 rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-4 py-4 transition-colors',
            'hover:border-primary/60 hover:bg-surface-container-low',
          )}
        >
          <div>
            <p className="font-semibold text-on-surface">Inbox mittenti</p>
            <p className="mt-1 text-xs text-on-surface-variant">
              Più indirizzi sullo stesso dominio verificato. Round-robin automatico
              con cap giornaliero per inbox e pausa auto su errori Resend.
            </p>
          </div>
          <span className="text-on-surface-variant group-hover:text-primary">→</span>
        </Link>

        {/* ── Multi-domain outreach (Sprint 6.2) ── */}
        <Link
          href="/settings/email-domains"
          className={cn(
            'group flex items-start justify-between gap-4 rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-4 py-4 transition-colors',
            'hover:border-primary/60 hover:bg-surface-container-low',
          )}
        >
          <div>
            <p className="font-semibold text-on-surface">Domini email</p>
            <p className="mt-1 text-xs text-on-surface-variant">
              Gestisci brand domain (Resend) e outreach domain (Gmail OAuth) con
              verifica DNS live SPF/DKIM/DMARC e tracking host per-dominio.
            </p>
          </div>
          <span className="text-on-surface-variant group-hover:text-primary">→</span>
        </Link>

        {/* ── Branding email ── */}
        <Link
          href="/settings/branding"
          className={cn(
            'group flex items-start justify-between gap-4 rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-4 py-4 transition-colors',
            'hover:border-primary/60 hover:bg-surface-container-low',
          )}
        >
          <div>
            <p className="font-semibold text-on-surface">Branding email</p>
            <p className="mt-1 text-xs text-on-surface-variant">
              Colore principale, logo e nome mittente con anteprima live del
              template. Nessun wizard richiesto.
            </p>
          </div>
          <span className="text-on-surface-variant group-hover:text-primary">→</span>
        </Link>

        {/* ── Email domain ── */}
        <Link
          href="/settings/email-domain"
          className={cn(
            'group flex items-start justify-between gap-4 rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-4 py-4 transition-colors',
            'hover:border-primary/60 hover:bg-surface-container-low',
          )}
        >
          <div>
            <p className="font-semibold text-on-surface">Dominio mittente</p>
            <p className="mt-1 text-xs text-on-surface-variant">
              Configura{' '}
              <span className="font-mono">outreach@tuodominio.it</span> con record
              SPF / DKIM tramite Resend. Verifica DNS guidata.
            </p>
          </div>
          <span className="text-on-surface-variant group-hover:text-primary">
            {tenant.email_from_domain ? (
              <span className="text-xs font-semibold text-primary">
                {tenant.email_from_domain}
              </span>
            ) : (
              '→'
            )}
          </span>
        </Link>

        <Link
          href="/settings/crm-webhooks"
          className={cn(
            'group flex items-start justify-between gap-4 rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-4 py-4 transition-colors',
            'hover:border-primary/60 hover:bg-surface-container-low',
          )}
        >
          <div>
            <p className="font-semibold text-on-surface">Webhook CRM in uscita</p>
            <p className="mt-1 text-xs text-on-surface-variant">
              HMAC-SHA256, retry con backoff, disattivazione automatica dopo 10
              fallimenti consecutivi.
            </p>
          </div>
          <span className="text-on-surface-variant group-hover:text-primary">
            {crmAllowed ? '→' : '🔒'}
          </span>
        </Link>

        <Link
          href="/settings/privacy"
          className={cn(
            'group flex items-start justify-between gap-4 rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-4 py-4 transition-colors',
            'hover:border-primary/60 hover:bg-surface-container-low',
          )}
        >
          <div>
            <p className="font-semibold text-on-surface">Privacy e GDPR</p>
            <p className="mt-1 text-xs text-on-surface-variant">
              Log di audit immutabile, esporta/elimina dati lead per le
              richieste dei soggetti interessati (Art. 15 / 17).
            </p>
          </div>
          <span className="text-on-surface-variant group-hover:text-primary">→</span>
        </Link>

        <Link
          href="/experiments"
          className={cn(
            'group flex items-start justify-between gap-4 rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-4 py-4 transition-colors',
            'hover:border-primary/60 hover:bg-surface-container-low',
          )}
        >
          <div>
            <p className="font-semibold text-on-surface">A/B Testing email</p>
            <p className="mt-1 text-xs text-on-surface-variant">
              Testa due oggetti email in parallelo con analisi Bayesiana
              automatica. Dichiara il vincitore con ≥95% di confidenza.
            </p>
          </div>
          <span className="text-on-surface-variant group-hover:text-primary">
            {canTenantUse(tenant, 'ab_testing_templates') ? '→' : '🔒'}
          </span>
        </Link>
      </div>
    </BentoCard>
  );
}

// ---------------------------------------------------------------------------
// Reputation dominio
// ---------------------------------------------------------------------------

function ReputationAlarmBanner({ reputation }: { reputation: DomainReputationRow }) {
  const problems: string[] = [];
  if (reputation.alarm_bounce) {
    problems.push(
      `bounce rate al ${formatPercentPrecise(reputation.bounce_rate)} (soglia 5%)`,
    );
  }
  if (reputation.alarm_complaint) {
    problems.push(
      `segnalazioni spam al ${formatPercentPrecise(reputation.complaint_rate)} (soglia 0.3%)`,
    );
  }
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-lg border border-error/40 bg-error-container/40 px-4 py-3 text-sm text-on-error-container"
    >
      <span aria-hidden className="mt-0.5 text-lg leading-none">⚠️</span>
      <div className="flex-1">
        <p className="font-semibold">Reputazione del mittente a rischio</p>
        <p className="mt-1 text-on-error-container/90">
          Negli ultimi 7 giorni: {problems.join(' e ')}. Continuando a questi
          ritmi il provider potrebbe sospendere l&apos;invio. Abbassa il volume
          outreach o verifica la qualità delle liste prima di procedere.
        </p>
      </div>
    </div>
  );
}

function ReputationCard({
  reputation,
  tenant,
  domainSwitched,
}: {
  reputation: DomainReputationRow | null;
  tenant: TenantRow;
  domainSwitched: boolean;
}) {
  const verifiedAt = tenant.email_from_domain_verified_at ?? null;
  const warmup = resolveWarmupState(verifiedAt);
  const activeDomain = tenant.email_from_domain;

  return (
    <BentoCard span="full">
      <div className="flex flex-col gap-1 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Reputazione dominio
          </p>
          <h2 className="font-headline text-2xl font-bold tracking-tighter">
            {activeDomain ?? 'Dominio non configurato'}
          </h2>
          <p className="mt-1 max-w-xl text-sm text-on-surface-variant">
            {activeDomain
              ? 'Salute delle mail outreach nelle ultime 7 giornate. Aggiornato ogni notte alle 02:30 UTC.'
              : 'Configura il dominio mittente nelle impostazioni SMTP per sbloccare le metriche di reputazione.'}
          </p>
        </div>
        <WarmupBadge state={warmup} />
      </div>

      {domainSwitched && reputation && (
        <p className="mt-4 rounded-md border border-dashed border-on-surface-variant/30 px-3 py-2 text-xs text-on-surface-variant">
          La reputazione mostra ancora i dati del dominio precedente
          (<span className="font-mono">{reputation.email_from_domain}</span>).
          I nuovi invii verranno aggregati sotto <span className="font-mono">{activeDomain}</span>
          al prossimo rollup.
        </p>
      )}

      {!reputation ? (
        <p className="mt-6 text-sm text-on-surface-variant">
          Nessuno snapshot disponibile. Il primo rollup notturno genererà le
          metriche — nel frattempo continua pure con gli invii, il rate-limiter
          applica comunque la curva di warm-up per proteggere il dominio.
        </p>
      ) : (
        <dl className="mt-6 grid grid-cols-2 gap-4 md:grid-cols-4">
          <ReputationMetric
            label="Inviate (7gg)"
            value={reputation.sent_count.toLocaleString('it-IT')}
          />
          <ReputationMetric
            label="Consegnate"
            value={formatPercentPrecise(reputation.delivery_rate)}
            tone={reputation.delivery_rate != null && reputation.delivery_rate < 0.9 ? 'warn' : 'ok'}
          />
          <ReputationMetric
            label="Bounce"
            value={formatPercentPrecise(reputation.bounce_rate)}
            tone={reputation.alarm_bounce ? 'alarm' : 'ok'}
          />
          <ReputationMetric
            label="Reclami spam"
            value={formatPercentPrecise(reputation.complaint_rate)}
            tone={reputation.alarm_complaint ? 'alarm' : 'ok'}
          />
        </dl>
      )}
    </BentoCard>
  );
}

function ReputationMetric({
  label,
  value,
  tone = 'ok',
}: {
  label: string;
  value: string;
  tone?: 'ok' | 'warn' | 'alarm';
}) {
  const toneClass = {
    ok: 'text-on-surface',
    warn: 'text-tertiary',
    alarm: 'text-error',
  }[tone];
  return (
    <div>
      <dt className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
        {label}
      </dt>
      <dd
        className={cn(
          'mt-1 font-headline text-2xl font-bold tabular-nums tracking-tighter',
          toneClass,
        )}
      >
        {value}
      </dd>
    </div>
  );
}

type WarmupState =
  | { phase: 'cold' }
  | { phase: 'warming'; day: number; totalDays: number }
  | { phase: 'steady' };

function resolveWarmupState(verifiedAt: string | null): WarmupState {
  const WARMUP_DAYS = 7;
  if (!verifiedAt) return { phase: 'cold' };
  const verified = new Date(verifiedAt).getTime();
  if (Number.isNaN(verified)) return { phase: 'cold' };
  const diffDays = Math.floor((Date.now() - verified) / (1000 * 60 * 60 * 24));
  if (diffDays >= WARMUP_DAYS) return { phase: 'steady' };
  return {
    phase: 'warming',
    day: Math.max(1, diffDays + 1),
    totalDays: WARMUP_DAYS,
  };
}

function WarmupBadge({ state }: { state: WarmupState }) {
  if (state.phase === 'steady') {
    return (
      <span className="rounded-full bg-primary-container px-3 py-1 text-xs font-semibold text-on-primary-container">
        Dominio stabile
      </span>
    );
  }
  if (state.phase === 'warming') {
    return (
      <span className="rounded-full bg-tertiary-container px-3 py-1 text-xs font-semibold text-on-tertiary-container">
        Warm-up · giorno {state.day} / {state.totalDays}
      </span>
    );
  }
  return (
    <span className="rounded-full border border-error/40 bg-error-container/40 px-3 py-1 text-xs font-semibold text-on-error-container">
      Dominio non verificato
    </span>
  );
}

function formatPercentPrecise(v: number | null | undefined): string {
  if (v == null) return '—';
  // 1 decimale a basso volume / 0 ad alto — più stabile visivamente.
  const pct = v * 100;
  if (pct >= 10) return `${pct.toFixed(0)}%`;
  return `${pct.toFixed(1)}%`;
}

// ---------------------------------------------------------------------------
// Plan / tier card
// ---------------------------------------------------------------------------

const CAPABILITY_LABELS: Array<{ key: CapabilityKey; label: string; hint?: string }> = [
  { key: 'email_outreach', label: 'Outreach email' },
  { key: 'postal_outreach', label: 'Outreach cartolina postale' },
  { key: 'whatsapp_outreach', label: 'Outreach WhatsApp' },
  { key: 'realtime_timeline', label: 'Timeline live', hint: 'Aggiornamenti istantanei su ogni lead' },
  { key: 'advanced_analytics', label: 'Analytics avanzate' },
  { key: 'crm_outbound_webhooks', label: 'Webhook CRM' },
  { key: 'custom_brand_domain', label: 'Dominio mittente personalizzato' },
  { key: 'bulk_export', label: 'Export massivo lead' },
  { key: 'template_editor', label: 'Editor template', hint: 'Modifica l\u2019HTML/Jinja delle mail outreach' },
  { key: 'ab_testing_templates', label: 'A/B test template' },
  { key: 'api_access', label: 'API programmatica' },
];

function PlanCard({ tenant }: { tenant: TenantRow }) {
  const snapshot = resolveTierSnapshot(tenant);
  const currentIdx = TIER_ORDER.indexOf(snapshot.tier);
  const upgradeMailto = `mailto:upgrade@solarlead.it?subject=${encodeURIComponent(
    `Upgrade piano — ${tenant.business_name}`,
  )}`;

  return (
    <BentoCard span="full" id="plan">
      <div className="flex flex-col gap-1 md:flex-row md:items-end md:justify-between">
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Il tuo piano
          </p>
          <h2 className="font-headline text-2xl font-bold tracking-tighter">
            {TIER_LABEL[snapshot.tier]}
            {snapshot.hasOverrides && (
              <span className="ml-2 text-xs font-medium text-on-surface-variant">
                · feature flag attivi
              </span>
            )}
          </h2>
          <p className="mt-1 max-w-xl text-sm text-on-surface-variant">
            Per cambiare piano contatta il nostro team commerciale — l&apos;attivazione
            è manuale (niente carte, niente fatture auto-generate da subito).
          </p>
        </div>
        {snapshot.tier !== 'enterprise' && (
          <GradientButton variant="primary" size="md" href={upgradeMailto}>
            Richiedi upgrade
          </GradientButton>
        )}
      </div>

      <div className="mt-6 overflow-hidden rounded-lg bg-surface-container-lowest">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
              <th className="px-5 py-3">Funzione</th>
              {TIER_ORDER.map((t, i) => (
                <th
                  key={t}
                  className={cn(
                    'w-28 px-3 py-3 text-center',
                    i === currentIdx && 'text-primary',
                  )}
                >
                  {TIER_LABEL[t]}
                  {i === currentIdx && (
                    <span className="ml-1 rounded-full bg-primary-container px-1.5 py-0.5 text-[9px] text-on-primary-container">
                      Attivo
                    </span>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {CAPABILITY_LABELS.map((row, idx) => (
              <PlanRow
                key={row.key}
                label={row.label}
                hint={row.hint}
                values={TIER_ORDER.map((t) => tierHasCapability(t, row.key))}
                currentIdx={currentIdx}
                dividerTop={idx !== 0}
              />
            ))}
          </tbody>
        </table>
      </div>
    </BentoCard>
  );
}

/**
 * Runtime-reconstruct the static matrix. Kept inline here (instead of
 * exporting from tier.ts) because these labels are UI-only.
 */
function tierHasCapability(tier: TenantTier, key: CapabilityKey): boolean {
  return resolveTierSnapshot({
    id: '',
    business_name: '',
    brand_primary_color: null,
    brand_logo_url: null,
    contact_email: '',
    whatsapp_number: null,
    email_from_domain: null,
    email_from_name: null,
    tier,
    settings: {},
  }).capabilities[key];
}

// ---------------------------------------------------------------------------
// Dev tools — visible only to super_admin
// ---------------------------------------------------------------------------

function DevToolsCard() {
  return (
    <BentoCard span="full">
      <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
        Dev tools · solo super_admin
      </p>
      <h2 className="mt-1 font-headline text-2xl font-bold tracking-tighter">
        Strumenti di test
      </h2>
      <p className="mt-1 max-w-xl text-sm text-on-surface-variant">
        Inietta dati sintetici nel pipeline per verificare scoring → rendering → email
        senza passare da Atoka o Google Solar.
      </p>
      <div className="mt-5">
        <Link
          href="/settings/pipeline-test"
          className="inline-flex items-center gap-2 rounded-lg bg-primary px-5 py-2.5 text-sm font-semibold text-on-primary transition-opacity hover:opacity-90"
        >
          🚀 Test pipeline end-to-end
        </Link>
      </div>
    </BentoCard>
  );
}

function PlanRow({
  label,
  hint,
  values,
  currentIdx,
  dividerTop,
}: {
  label: string;
  hint?: string;
  values: boolean[];
  currentIdx: number;
  dividerTop: boolean;
}) {
  return (
    <tr
      style={
        dividerTop ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' } : undefined
      }
    >
      <td className="px-5 py-3">
        <p className="font-medium text-on-surface">{label}</p>
        {hint && <p className="text-xs text-on-surface-variant">{hint}</p>}
      </td>
      {values.map((yes, i) => (
        <td
          key={i}
          className={cn(
            'px-3 py-3 text-center',
            i === currentIdx && 'bg-primary-container/30',
          )}
        >
          {yes ? (
            <span className="text-primary" aria-label="incluso">
              ✓
            </span>
          ) : (
            <span className="text-on-surface-variant/50" aria-label="non incluso">
              —
            </span>
          )}
        </td>
      ))}
    </tr>
  );
}
