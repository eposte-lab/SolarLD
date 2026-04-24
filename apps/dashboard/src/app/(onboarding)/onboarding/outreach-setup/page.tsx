'use client';

/**
 * /onboarding/outreach-setup — Multi-step outreach domain & inbox wizard (Sprint 6.4)
 *
 * Steps:
 *   1. Domains — enter 1-2 dedicated outreach domains (different from brand)
 *   2. DNS      — per-domain DNS record table + live check (SPF / DKIM / DMARC / tracking)
 *   3. Workspace — instructions to create Google Workspace accounts
 *   4. OAuth    — connect each inbox via Gmail OAuth
 *   5. Test     — send a test email from each inbox
 *
 * Design: each step is shown one at a time with a progress bar.
 * The user can proceed only when the step's minimum requirements are met.
 * No data loss on back-navigation: all state lives in component memory.
 */

import { useState, useTransition } from 'react';
import Link from 'next/link';
import { apiClient } from '@/lib/api-client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface DomainEntry {
  name: string;       // e.g. "agendasolar.it"
  trackingHost: string; // e.g. "go.agendasolar.it"
  domainId: string | null;  // set after POST /v1/email-domains
  dnsChecked: boolean;
  spfOk: boolean;
  dkimOk: boolean;
  dmarcOk: boolean;
  trackingOk: boolean;
}

interface InboxEntry {
  id: string;
  email: string;
  displayName: string;
  provider: string;
  oauthConnected: boolean;
  oauthError: string | null;
}

type Step = 1 | 2 | 3 | 4 | 5;

// ---------------------------------------------------------------------------
// Progress bar
// ---------------------------------------------------------------------------

const STEP_LABELS = ['Domini', 'DNS', 'Workspace', 'OAuth', 'Test'];

function ProgressBar({ step }: { step: Step }) {
  return (
    <div className="flex items-center gap-0">
      {STEP_LABELS.map((label, i) => {
        const n = (i + 1) as Step;
        const done = n < step;
        const active = n === step;
        return (
          <div key={label} className="flex items-center">
            <div className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-bold transition-colors ${
              done ? 'bg-primary text-on-primary' :
              active ? 'bg-primary-container text-on-primary-container ring-2 ring-primary' :
              'bg-surface-container-high text-on-surface-variant'
            }`}>
              {done ? '✓' : n}
            </div>
            <span className={`ml-1.5 mr-3 hidden text-xs font-medium sm:inline ${
              active ? 'text-on-surface' : 'text-on-surface-variant'
            }`}>
              {label}
            </span>
            {i < STEP_LABELS.length - 1 && (
              <div className={`mr-3 h-0.5 w-6 sm:w-10 ${done ? 'bg-primary' : 'bg-outline-variant/40'}`} />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function OutreachSetupPage() {
  const [step, setStep] = useState<Step>(1);
  const [domains, setDomains] = useState<DomainEntry[]>([
    { name: '', trackingHost: '', domainId: null, dnsChecked: false, spfOk: false, dkimOk: false, dmarcOk: false, trackingOk: false },
  ]);
  const [inboxes, setInboxes] = useState<InboxEntry[]>([]);
  const [testResults, setTestResults] = useState<Record<string, 'ok' | 'error' | 'sending'>>({});
  const [, startTransition] = useTransition();

  function goNext() { setStep((s) => Math.min(5, s + 1) as Step); }
  function goBack() { setStep((s) => Math.max(1, s - 1) as Step); }

  return (
    <div className="mx-auto max-w-3xl space-y-8 px-4 py-10">
      {/* Header */}
      <div>
        <Link href="/onboarding" className="text-xs text-on-surface-variant hover:underline">
          ← Onboarding
        </Link>
        <h1 className="mt-3 font-headline text-4xl font-bold tracking-tighter">
          Setup outreach
        </h1>
        <p className="mt-2 text-sm text-on-surface-variant">
          Configura i tuoi domini dedicati all&apos;outreach cold (separate dal brand)
          con inbox Gmail reali. In ~90 minuti avrai una struttura che può inviare
          250+ email/giorno qualificate con reputation individuale.
        </p>
      </div>

      {/* Progress */}
      <ProgressBar step={step} />

      {/* Steps */}
      {step === 1 && (
        <StepDomains
          domains={domains}
          onChange={setDomains}
          onNext={goNext}
        />
      )}
      {step === 2 && (
        <StepDns
          domains={domains}
          onChange={setDomains}
          onNext={goNext}
          onBack={goBack}
        />
      )}
      {step === 3 && (
        <StepWorkspace
          domains={domains}
          inboxes={inboxes}
          onInboxesChange={setInboxes}
          onNext={goNext}
          onBack={goBack}
        />
      )}
      {step === 4 && (
        <StepOAuth
          inboxes={inboxes}
          onInboxesChange={setInboxes}
          onNext={goNext}
          onBack={goBack}
        />
      )}
      {step === 5 && (
        <StepTest
          inboxes={inboxes}
          testResults={testResults}
          onTestResultsChange={setTestResults}
          startTransition={startTransition}
          onDone={() => { window.location.href = '/settings/email-domains'; }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 1 — Domains
// ---------------------------------------------------------------------------

function StepDomains({
  domains,
  onChange,
  onNext,
}: {
  domains: DomainEntry[];
  onChange: (d: DomainEntry[]) => void;
  onNext: () => void;
}) {
  const [saving, setSaving] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  function updateDomain(index: number, patch: Partial<DomainEntry>) {
    onChange(domains.map((d, i) => i === index ? { ...d, ...patch } : d));
  }

  function handleNameChange(index: number, val: string) {
    const name = val.trim();
    const auto = domains[index].trackingHost === '' || domains[index].trackingHost === `go.${domains[index].name}`;
    updateDomain(index, {
      name,
      trackingHost: auto ? `go.${name}` : domains[index].trackingHost,
    });
  }

  function addDomain() {
    if (domains.length >= 3) return;
    onChange([...domains, { name: '', trackingHost: '', domainId: null, dnsChecked: false, spfOk: false, dkimOk: false, dmarcOk: false, trackingOk: false }]);
  }

  function removeDomain(index: number) {
    onChange(domains.filter((_, i) => i !== index));
  }

  async function handleNext() {
    setSaving(true);
    setErrors([]);
    const errs: string[] = [];
    const updated = [...domains];

    for (let i = 0; i < updated.length; i++) {
      const d = updated[i];
      if (!d.name.trim()) continue;
      if (d.domainId) continue; // Already created
      try {
        const res = await apiClient.post<{ id: string }>('/v1/email-domains', {
          domain: d.name.toLowerCase(),
          purpose: 'outreach',
          tracking_host: d.trackingHost.trim() || null,
          default_provider: 'gmail_oauth',
        });
        updated[i] = { ...updated[i], domainId: res.id };
      } catch (err: unknown) {
        errs.push(`${d.name}: ${err instanceof Error ? err.message : 'Errore'}`);
      }
    }

    onChange(updated);
    setSaving(false);
    if (errs.length === 0) {
      onNext();
    } else {
      setErrors(errs);
    }
  }

  const canProceed = domains.some((d) => d.name.trim().length > 3);

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-outline-variant/40 bg-surface-container-lowest p-6">
        <h2 className="font-headline text-xl font-bold tracking-tighter">
          Step 1 · Scegli i domini outreach
        </h2>
        <p className="mt-2 text-sm text-on-surface-variant">
          Usa 1-2 domini <strong>diversi dal tuo brand</strong> (es.{' '}
          <span className="font-mono">agendasolar.it</span>,{' '}
          <span className="font-mono">get-agenda.it</span>). Devono essere già
          acquistati. Se non li hai, comprane uno su Aruba o Cloudflare (~€10/anno).
        </p>

        <div className="mt-6 space-y-4">
          {domains.map((d, i) => (
            <div key={i} className="flex gap-3">
              <div className="flex-1 space-y-2">
                <input
                  type="text"
                  placeholder={`es. agendasolar${i > 0 ? (i + 1) : ''}.it`}
                  value={d.name}
                  onChange={(e) => handleNameChange(i, e.target.value)}
                  className="w-full rounded-lg bg-surface-container-low px-3 py-2.5 text-sm font-mono text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
                />
                <input
                  type="text"
                  placeholder={`go.${d.name || 'tuodominio.it'} (tracking host)`}
                  value={d.trackingHost}
                  onChange={(e) => updateDomain(i, { trackingHost: e.target.value })}
                  className="w-full rounded-lg bg-surface-container-low px-3 py-2.5 text-xs font-mono text-on-surface-variant outline-none focus:ring-2 focus:ring-primary/40"
                />
              </div>
              {domains.length > 1 && (
                <button
                  type="button"
                  onClick={() => removeDomain(i)}
                  className="self-start rounded-lg px-2.5 py-2.5 text-on-surface-variant hover:bg-surface-container-low"
                >
                  ✕
                </button>
              )}
            </div>
          ))}
        </div>

        {domains.length < 3 && (
          <button
            type="button"
            onClick={addDomain}
            className="mt-3 text-sm font-semibold text-primary hover:underline"
          >
            + Aggiungi secondo dominio
          </button>
        )}

        {errors.length > 0 && (
          <div className="mt-4 rounded-lg bg-error-container px-4 py-3 text-sm text-on-error-container">
            {errors.map((e, i) => <p key={i}>{e}</p>)}
          </div>
        )}
      </div>

      <div className="flex justify-end">
        <button
          type="button"
          disabled={!canProceed || saving}
          onClick={handleNext}
          className="rounded-full bg-gradient-primary px-6 py-3 text-sm font-bold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-90 disabled:opacity-40"
        >
          {saving ? 'Salvataggio…' : 'Avanti → Configura DNS'}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — DNS
// ---------------------------------------------------------------------------

interface DnsCheckResult {
  spf: { ok: boolean };
  dkim_resend: { ok: boolean };
  dkim_google: { ok: boolean };
  dmarc: { ok: boolean };
  tracking_cname: { ok: boolean };
}

function StepDns({
  domains,
  onChange,
  onNext,
  onBack,
}: {
  domains: DomainEntry[];
  onChange: (d: DomainEntry[]) => void;
  onNext: () => void;
  onBack: () => void;
}) {
  const [checking, setChecking] = useState<Record<number, boolean>>({});

  const activeDomains = domains.filter((d) => d.name.trim() && d.domainId);

  async function checkDns(index: number) {
    const d = activeDomains[index];
    if (!d.domainId) return;
    setChecking((c) => ({ ...c, [index]: true }));
    try {
      const res = await apiClient.post<DnsCheckResult>(
        `/v1/email-domains/${d.domainId}/dns-check`, {}
      );
      const globalIndex = domains.findIndex((dd) => dd.domainId === d.domainId);
      const updated = [...domains];
      updated[globalIndex] = {
        ...updated[globalIndex],
        dnsChecked: true,
        spfOk: res.spf.ok,
        dkimOk: res.dkim_resend.ok || res.dkim_google.ok,
        dmarcOk: res.dmarc.ok,
        trackingOk: res.tracking_cname.ok,
      };
      onChange(updated);
    } finally {
      setChecking((c) => ({ ...c, [index]: false }));
    }
  }

  const canProceed = activeDomains.some((d) => d.dnsChecked);

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-outline-variant/40 bg-surface-container-lowest p-6">
        <h2 className="font-headline text-xl font-bold tracking-tighter">
          Step 2 · Configura i record DNS
        </h2>
        <p className="mt-2 text-sm text-on-surface-variant">
          Copia questi record nel pannello DNS del tuo registrar (Aruba, Cloudflare,
          Register.it…). Usa TTL 300 così i cambiamenti propagano in pochi minuti.
          Non è necessario che siano tutti verdi per procedere — puoi tornare qui dopo.
        </p>

        {activeDomains.map((d, i) => (
          <div key={d.domainId} className="mt-6 border-t border-outline-variant/30 pt-6 first:border-0 first:pt-0">
            <div className="mb-4 flex items-center justify-between">
              <h3 className="font-mono text-base font-bold text-on-surface">{d.name}</h3>
              <button
                type="button"
                onClick={() => checkDns(i)}
                disabled={checking[i]}
                className="rounded-lg border border-primary px-3 py-1.5 text-xs font-semibold text-primary hover:bg-primary/10 disabled:opacity-50"
              >
                {checking[i] ? 'Verifica…' : 'Verifica DNS live'}
              </button>
            </div>

            {/* DNS records table */}
            <div className="overflow-x-auto rounded-lg border border-outline-variant/30">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-outline-variant/30 bg-surface-container-low">
                    <th className="px-3 py-2 text-left font-semibold uppercase tracking-wider text-on-surface-variant">Tipo</th>
                    <th className="px-3 py-2 text-left font-semibold uppercase tracking-wider text-on-surface-variant">Host</th>
                    <th className="px-3 py-2 text-left font-semibold uppercase tracking-wider text-on-surface-variant">Valore</th>
                    <th className="px-3 py-2 text-left font-semibold uppercase tracking-wider text-on-surface-variant">Stato</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-outline-variant/20">
                  <DnsRow
                    type="TXT" host={`@`}
                    value={`v=spf1 include:_spf.google.com ~all`}
                    ok={d.dnsChecked ? d.spfOk : null}
                    label="SPF"
                  />
                  <DnsRow
                    type="CNAME" host={`google._domainkey`}
                    value={`google._domainkey.${d.name} → goog-dkim-target`}
                    ok={d.dnsChecked ? d.dkimOk : null}
                    label="DKIM"
                    note="Ottieni il record CNAME esatto dalla tua Google Workspace Admin Console › Apps › Gmail › Authenticate email"
                  />
                  <DnsRow
                    type="TXT" host={`_dmarc`}
                    value={`v=DMARC1; p=none; rua=mailto:dmarc@${d.name}; pct=100`}
                    ok={d.dnsChecked ? d.dmarcOk : null}
                    label="DMARC"
                  />
                  {d.trackingHost && (
                    <DnsRow
                      type="CNAME" host={d.trackingHost}
                      value={`track.solarld.app`}
                      ok={d.dnsChecked ? d.trackingOk : null}
                      label="Tracking"
                    />
                  )}
                </tbody>
              </table>
            </div>

            {/* Status summary */}
            {d.dnsChecked && (
              <div className="mt-3 flex flex-wrap gap-2">
                <DnsPill label="SPF" ok={d.spfOk} />
                <DnsPill label="DKIM" ok={d.dkimOk} />
                <DnsPill label="DMARC" ok={d.dmarcOk} />
                {d.trackingHost && <DnsPill label="Tracking" ok={d.trackingOk} />}
              </div>
            )}
          </div>
        ))}
      </div>

      <div className="flex justify-between">
        <button type="button" onClick={onBack} className="rounded-full border border-outline-variant px-5 py-2.5 text-sm font-semibold text-on-surface hover:bg-surface-container-low">
          ← Indietro
        </button>
        <button
          type="button"
          onClick={onNext}
          className="rounded-full bg-gradient-primary px-6 py-3 text-sm font-bold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-90"
        >
          {canProceed ? 'Avanti → Google Workspace' : 'Salta (configura dopo) →'}
        </button>
      </div>
    </div>
  );
}

function DnsRow({
  type, host, value, ok, label, note,
}: {
  type: string; host: string; value: string; ok: boolean | null;
  label: string; note?: string;
}) {
  return (
    <tr className="align-top">
      <td className="px-3 py-2.5">
        <span className="rounded bg-surface-container-high px-1.5 py-0.5 font-mono text-[10px] font-semibold text-on-surface-variant">{type}</span>
      </td>
      <td className="px-3 py-2.5 font-mono text-on-surface">{host}</td>
      <td className="px-3 py-2.5 max-w-xs">
        <p className="break-all font-mono text-on-surface-variant">{value}</p>
        {note && <p className="mt-0.5 text-on-surface-variant/60 italic">{note}</p>}
      </td>
      <td className="px-3 py-2.5">
        {ok === null ? (
          <span className="text-on-surface-variant">—</span>
        ) : ok ? (
          <span className="text-primary font-semibold">✅</span>
        ) : (
          <span className="text-error font-semibold">❌</span>
        )}
      </td>
    </tr>
  );
}

function DnsPill({ label, ok }: { label: string; ok: boolean }) {
  return (
    <span className={`rounded-full px-2.5 py-1 text-[11px] font-semibold ${
      ok ? 'bg-primary-container text-on-primary-container' : 'bg-error-container text-on-error-container'
    }`}>
      {ok ? '✓' : '✗'} {label}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Step 3 — Google Workspace
// ---------------------------------------------------------------------------

function StepWorkspace({
  domains,
  inboxes,
  onInboxesChange,
  onNext,
  onBack,
}: {
  domains: DomainEntry[];
  inboxes: InboxEntry[];
  onInboxesChange: (i: InboxEntry[]) => void;
  onNext: () => void;
  onBack: () => void;
}) {
  const [newEmail, setNewEmail] = useState('');
  const [newName, setNewName] = useState('');
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);

  const activeDomains = domains.filter((d) => d.name.trim() && d.domainId);

  async function addInbox() {
    if (!newEmail.trim()) return;
    setAdding(true);
    setAddError(null);
    try {
      const res = await apiClient.post<{ inbox: { id: string; email: string; display_name: string; provider: string } }>('/v1/inboxes', {
        email: newEmail.trim().toLowerCase(),
        display_name: newName.trim(),
        daily_cap: 50,
        provider: 'gmail_oauth',
      });
      const inbox = res.inbox;
      onInboxesChange([...inboxes, {
        id: inbox.id,
        email: inbox.email,
        displayName: inbox.display_name,
        provider: inbox.provider,
        oauthConnected: false,
        oauthError: null,
      }]);
      setNewEmail('');
      setNewName('');
    } catch (err: unknown) {
      setAddError(err instanceof Error ? err.message : 'Errore');
    } finally {
      setAdding(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-outline-variant/40 bg-surface-container-lowest p-6 space-y-6">
        <div>
          <h2 className="font-headline text-xl font-bold tracking-tighter">
            Step 3 · Crea gli account Google Workspace
          </h2>
          <p className="mt-2 text-sm text-on-surface-variant">
            Per ogni dominio outreach, crea 3 account Gmail reali su Google Workspace
            (~€6/mese/account). Usa nomi reali (Alfonso, Gaetano, Sara) per sembrare
            umani. Non usare &quot;info&quot; o &quot;noreply&quot;.
          </p>
        </div>

        {/* Per-domain instructions */}
        {activeDomains.map((d) => (
          <div key={d.domainId} className="rounded-lg bg-surface-container-low p-4">
            <p className="font-mono text-sm font-bold text-on-surface">{d.name}</p>
            <ol className="mt-3 space-y-2 text-sm text-on-surface-variant list-decimal list-inside">
              <li>
                Vai su{' '}
                <a href="https://workspace.google.com" target="_blank" rel="noopener" className="text-primary underline">
                  workspace.google.com
                </a>{' '}
                e attiva il piano Business Starter (€6/utente/mese).
              </li>
              <li>
                Aggiungi il dominio <span className="font-mono">{d.name}</span> e completa la
                verifica proprietà (Google ti darà un record TXT da aggiungere al DNS).
              </li>
              <li>
                Crea 3 utenti con nomi umani es:{' '}
                <span className="font-mono">alfonso@{d.name}</span>,{' '}
                <span className="font-mono">gaetano@{d.name}</span>,{' '}
                <span className="font-mono">sara@{d.name}</span>.
              </li>
              <li>
                In <em>Admin Console › Apps › Gmail › Authenticate email</em> ottieni i
                record DKIM (2 CNAME) e aggiungili al DNS (vedi Step 2).
              </li>
            </ol>
          </div>
        ))}

        {/* Add inbox form */}
        <div>
          <p className="mb-3 text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
            Registra le inbox appena create
          </p>
          <div className="flex gap-3">
            <input
              type="email"
              placeholder="alfonso@agendasolar.it"
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
              className="flex-1 rounded-lg bg-surface-container-low px-3 py-2.5 text-sm font-mono text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
            />
            <input
              type="text"
              placeholder="Alfonso Gallo"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              className="w-40 rounded-lg bg-surface-container-low px-3 py-2.5 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
            />
            <button
              type="button"
              onClick={addInbox}
              disabled={adding || !newEmail.trim()}
              className="rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-on-primary disabled:opacity-40"
            >
              {adding ? '…' : 'Aggiungi'}
            </button>
          </div>
          {addError && (
            <p className="mt-2 text-xs text-error">{addError}</p>
          )}

          {/* Inbox list */}
          {inboxes.length > 0 && (
            <div className="mt-3 space-y-2">
              {inboxes.map((inbox) => (
                <div key={inbox.id} className="flex items-center justify-between rounded-lg border border-outline-variant/30 px-3 py-2">
                  <div>
                    <span className="font-mono text-sm text-on-surface">{inbox.email}</span>
                    {inbox.displayName && (
                      <span className="ml-2 text-xs text-on-surface-variant">· {inbox.displayName}</span>
                    )}
                  </div>
                  <span className="rounded-full bg-tertiary-container px-2 py-0.5 text-[10px] font-semibold text-on-tertiary-container">
                    Da collegare
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="flex justify-between">
        <button type="button" onClick={onBack} className="rounded-full border border-outline-variant px-5 py-2.5 text-sm font-semibold text-on-surface hover:bg-surface-container-low">
          ← Indietro
        </button>
        <button
          type="button"
          onClick={onNext}
          className="rounded-full bg-gradient-primary px-6 py-3 text-sm font-bold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-90"
        >
          {inboxes.length > 0 ? 'Avanti → Connetti Gmail' : 'Salta (configura dopo) →'}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 4 — OAuth Connect
// ---------------------------------------------------------------------------

function StepOAuth({
  inboxes,
  onInboxesChange,
  onNext,
  onBack,
}: {
  inboxes: InboxEntry[];
  onInboxesChange: (i: InboxEntry[]) => void;
  onNext: () => void;
  onBack: () => void;
}) {
  const [connecting, setConnecting] = useState<Record<string, boolean>>({});

  async function handleConnect(inbox: InboxEntry) {
    setConnecting((c) => ({ ...c, [inbox.id]: true }));
    try {
      const res = await apiClient.post<{ authorize_url: string }>(
        `/v1/inboxes/${inbox.id}/oauth/gmail/authorize`, {}
      );
      // Redirect to Google OAuth — user comes back to /settings/inboxes after auth.
      // We open in same tab so session cookie is preserved.
      window.location.href = res.authorize_url;
    } catch {
      setConnecting((c) => ({ ...c, [inbox.id]: false }));
    }
  }

  const connectedCount = inboxes.filter((i) => i.oauthConnected).length;

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-outline-variant/40 bg-surface-container-lowest p-6">
        <h2 className="font-headline text-xl font-bold tracking-tighter">
          Step 4 · Connetti le inbox via Gmail OAuth
        </h2>
        <p className="mt-2 text-sm text-on-surface-variant">
          Clicca &quot;Connetti Gmail&quot; per ogni inbox. Google ti chiederà
          di autorizzare SolarLead a inviare email a tuo nome. Il token viene
          crittografato e salvato in modo sicuro.
        </p>

        {inboxes.length === 0 ? (
          <div className="mt-6 rounded-lg bg-surface-container-low px-4 py-6 text-center">
            <p className="text-sm text-on-surface-variant">
              Nessuna inbox aggiunta nello step precedente.{' '}
              <button type="button" onClick={onBack} className="text-primary underline">
                Torna indietro
              </button>{' '}
              per aggiungerle.
            </p>
          </div>
        ) : (
          <div className="mt-6 space-y-3">
            {inboxes.map((inbox) => (
              <div
                key={inbox.id}
                className="flex items-center justify-between rounded-lg border border-outline-variant/30 px-4 py-3"
              >
                <div>
                  <p className="font-mono text-sm font-semibold text-on-surface">{inbox.email}</p>
                  {inbox.displayName && (
                    <p className="text-xs text-on-surface-variant">{inbox.displayName}</p>
                  )}
                </div>
                {inbox.oauthConnected ? (
                  <span className="rounded-full bg-primary-container px-3 py-1 text-xs font-semibold text-on-primary-container">
                    Gmail OAuth ✓
                  </span>
                ) : (
                  <button
                    type="button"
                    disabled={connecting[inbox.id]}
                    onClick={() => handleConnect(inbox)}
                    className="flex items-center gap-1.5 rounded-lg border border-primary/60 bg-primary-container/20 px-3 py-1.5 text-xs font-semibold text-primary hover:bg-primary/10 disabled:opacity-50"
                  >
                    <GoogleIcon />
                    {connecting[inbox.id] ? 'Reindirizzamento…' : 'Connetti Gmail'}
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {connectedCount > 0 && (
          <p className="mt-4 text-sm text-primary">
            ✓ {connectedCount}/{inboxes.length} inbox collegate.
          </p>
        )}
      </div>

      <div className="flex justify-between">
        <button type="button" onClick={onBack} className="rounded-full border border-outline-variant px-5 py-2.5 text-sm font-semibold text-on-surface hover:bg-surface-container-low">
          ← Indietro
        </button>
        <button
          type="button"
          onClick={onNext}
          className="rounded-full bg-gradient-primary px-6 py-3 text-sm font-bold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-90"
        >
          {connectedCount > 0 ? 'Avanti → Test invio' : 'Salta per ora →'}
        </button>
      </div>
    </div>
  );
}

function GoogleIcon() {
  return (
    <svg className="h-3 w-3" viewBox="0 0 24 24" fill="currentColor">
      <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/>
      <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/>
      <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l2.85-2.22.81-.62z"/>
      <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.47 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/>
    </svg>
  );
}

// ---------------------------------------------------------------------------
// Step 5 — Test send
// ---------------------------------------------------------------------------

function StepTest({
  inboxes,
  testResults,
  onTestResultsChange,
  startTransition,
  onDone,
}: {
  inboxes: InboxEntry[];
  testResults: Record<string, 'ok' | 'error' | 'sending'>;
  onTestResultsChange: (r: Record<string, 'ok' | 'error' | 'sending'>) => void;
  startTransition: (fn: () => void) => void;
  onDone: () => void;
}) {
  const [testEmail, setTestEmail] = useState('');

  function handleTest(inboxId: string) {
    if (!testEmail.trim()) return;
    onTestResultsChange({ ...testResults, [inboxId]: 'sending' });
    startTransition(async () => {
      try {
        await apiClient.post(`/v1/inboxes/${inboxId}/test-send`, { to: testEmail.trim() });
        onTestResultsChange({ ...testResults, [inboxId]: 'ok' });
      } catch {
        onTestResultsChange({ ...testResults, [inboxId]: 'error' });
      }
    });
  }

  const allOk = inboxes.length > 0 && inboxes.every((i) => testResults[i.id] === 'ok');

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-outline-variant/40 bg-surface-container-lowest p-6 space-y-5">
        <div>
          <h2 className="font-headline text-xl font-bold tracking-tighter">
            Step 5 · Test invio
          </h2>
          <p className="mt-2 text-sm text-on-surface-variant">
            Inserisci il tuo indirizzo email e invia un test da ogni inbox.
            Verifica che l&apos;email arrivi in inbox (non spam) e che il mittente
            mostri il nome corretto.
          </p>
        </div>

        <div>
          <label className="text-xs font-semibold uppercase tracking-wider text-on-surface-variant">
            Il tuo indirizzo email (per il test)
          </label>
          <input
            type="email"
            value={testEmail}
            onChange={(e) => setTestEmail(e.target.value)}
            placeholder="tu@gmail.com"
            className="mt-1 w-full rounded-lg bg-surface-container-low px-3 py-2.5 text-sm text-on-surface outline-none focus:ring-2 focus:ring-primary/40"
          />
        </div>

        {inboxes.length === 0 ? (
          <p className="text-sm text-on-surface-variant">
            Nessuna inbox da testare. Puoi inviare test dalla pagina{' '}
            <Link href="/settings/inboxes" className="text-primary underline">Inbox mittenti</Link>.
          </p>
        ) : (
          <div className="space-y-3">
            {inboxes.map((inbox) => {
              const result = testResults[inbox.id];
              return (
                <div
                  key={inbox.id}
                  className="flex items-center justify-between rounded-lg border border-outline-variant/30 px-4 py-3"
                >
                  <span className="font-mono text-sm text-on-surface">{inbox.email}</span>
                  <div className="flex items-center gap-3">
                    {result === 'ok' && (
                      <span className="text-xs font-semibold text-primary">✅ Inviata</span>
                    )}
                    {result === 'error' && (
                      <span className="text-xs font-semibold text-error">❌ Errore</span>
                    )}
                    <button
                      type="button"
                      disabled={!testEmail.trim() || result === 'sending'}
                      onClick={() => handleTest(inbox.id)}
                      className="rounded-lg border border-outline-variant px-3 py-1.5 text-xs font-semibold text-on-surface hover:bg-surface-container-low disabled:opacity-40"
                    >
                      {result === 'sending' ? 'Invio…' : result === 'ok' ? 'Reinvia' : 'Invia test'}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {allOk && (
          <div className="rounded-lg bg-primary-container/30 px-4 py-3 text-sm text-on-primary-container">
            🎉 Tutte le inbox funzionano. Il tuo sistema outreach è pronto!
            La curva di warm-up partirà automaticamente dal primo invio reale
            (10 → 25 → 40 → 50 email/inbox/giorno in 21 giorni).
          </div>
        )}
      </div>

      <div className="flex justify-end">
        <button
          type="button"
          onClick={onDone}
          className="rounded-full bg-gradient-primary px-6 py-3 text-sm font-bold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-90"
        >
          {allOk ? 'Setup completato → Vai ai domini' : 'Vai ai domini →'}
        </button>
      </div>
    </div>
  );
}
