'use client';

/**
 * EmailDomainManager — configure custom sending domain with Resend.
 *
 * Flow:
 *   1. Operator enters domain name + email_from_name
 *   2. POST /v1/branding/domain/setup → Resend creates the domain, returns DNS records
 *   3. Operator copies DNS records to their provider
 *   4. Click "Ricontrolla" → GET /v1/branding/domain/status → badge updates
 *
 * We never redirect or hard-reload — all state lives here.
 */

import { useCallback, useState } from 'react';

import { api } from '@/lib/api-client';
import { cn } from '@/lib/utils';

// ------------------------------------------------------------------ types

interface DnsRecord {
  type: string;
  name: string;
  value: string;
  priority: number | null;
  ttl: number | null;
  status: string;
}

interface DomainStatus {
  domain_id: string;
  domain: string;
  status: string;           // not_started | pending | verified | failed
  dns_records: DnsRecord[];
  created_at: string | null;
}

// ------------------------------------------------------------------ helpers

function statusBadge(s: string) {
  const map: Record<string, string> = {
    verified: 'bg-primary-container text-on-primary-container',
    pending: 'bg-tertiary-container text-on-tertiary-container',
    not_started: 'bg-surface-container-high text-on-surface-variant',
    failed: 'bg-error-container text-on-error-container',
  };
  const labels: Record<string, string> = {
    verified: '✓ Verificato',
    pending: '⏳ In attesa DNS',
    not_started: '○ Non avviato',
    failed: '✗ Verifica fallita',
  };
  return { cls: map[s] ?? map.not_started, label: labels[s] ?? s };
}

function recordStatusDot(s: string) {
  if (s === 'verified') return 'bg-primary';
  if (s === 'pending') return 'bg-tertiary animate-pulse';
  if (s === 'failed') return 'bg-error';
  return 'bg-on-surface-variant/40';
}

// ------------------------------------------------------------------ component

interface EmailDomainManagerProps {
  initialDomain: string | null;
  initialStatus: DomainStatus | null;
}

export function EmailDomainManager({
  initialDomain,
  initialStatus,
}: EmailDomainManagerProps) {
  const [domain, setDomain] = useState(initialDomain ?? '');
  const [fromName, setFromName] = useState('');
  const [domainStatus, setDomainStatus] = useState<DomainStatus | null>(
    initialStatus,
  );
  const [loading, setLoading] = useState(false);
  const [checking, setChecking] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  /**
   * Basic domain syntax guard.
   * Accepts: `agenda-pro.it`, `mail.agenda-pro.it`, `a.b.c.io`.
   * Rejects: anything with `@` (that's an email), whitespace, protocols,
   * paths, or labels that don't match RFC-ish shape.
   */
  function validateDomain(input: string): string | null {
    const v = input.trim().toLowerCase();
    if (!v) return 'Inserisci un dominio.';
    if (v.includes('@')) {
      return 'Inserisci solo il dominio, non un indirizzo email. Esempio: mail.agenda-pro.it';
    }
    if (/\s/.test(v)) return 'Il dominio non può contenere spazi.';
    if (v.startsWith('http://') || v.startsWith('https://') || v.includes('/')) {
      return 'Inserisci solo il dominio, senza http:// o slash. Esempio: mail.agenda-pro.it';
    }
    // label.label(.label)+ — each label 1-63 chars, alnum + hyphen, no leading/trailing hyphen
    const domainRe =
      /^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/;
    if (!domainRe.test(v)) {
      return 'Formato dominio non valido. Esempio: mail.tuodominio.it';
    }
    return null;
  }

  async function handleSetup() {
    const trimmed = domain.trim().toLowerCase();
    const validationError = validateDomain(trimmed);
    if (validationError) {
      setError(validationError);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const result = await api.post<DomainStatus>('/v1/branding/domain/setup', {
        domain: trimmed,
        email_from_name: fromName.trim() || null,
      });
      setDomainStatus(result);
    } catch (e) {
      // `fetch()` network failures surface as TypeError("Failed to fetch") — no
      // HTTP status involved. Distinguish so the user isn't told to "disconnect
      // the domain" when the real issue is the API being unreachable.
      const err = e as Error & { status?: number };
      if (err.name === 'TypeError' || /failed to fetch/i.test(err.message)) {
        setError(
          'Impossibile contattare il server. Verifica la connessione o riprova tra qualche istante.',
        );
      } else {
        setError(err.message);
      }
    } finally {
      setLoading(false);
    }
  }

  const handleCheck = useCallback(async () => {
    setChecking(true);
    setError(null);
    try {
      const result = await api.get<DomainStatus>('/v1/branding/domain/status');
      setDomainStatus(result);
    } catch (e) {
      const err = e as Error;
      if (err.name === 'TypeError' || /failed to fetch/i.test(err.message)) {
        setError(
          'Impossibile contattare il server. Verifica la connessione o riprova tra qualche istante.',
        );
      } else {
        setError(err.message);
      }
    } finally {
      setChecking(false);
    }
  }, []);

  const handleDisconnect = useCallback(async () => {
    if (
      !confirm(
        'Disconnettere il dominio? Verrà rimosso da Resend e i record DNS non saranno più attivi. Potrai riconfigurarlo in qualsiasi momento.',
      )
    ) {
      return;
    }
    setDisconnecting(true);
    setError(null);
    try {
      await api.delete('/v1/branding/domain');
      setDomainStatus(null);
      setDomain('');
    } catch (e) {
      const err = e as Error;
      if (err.name === 'TypeError' || /failed to fetch/i.test(err.message)) {
        setError(
          'Impossibile contattare il server. Verifica la connessione o riprova tra qualche istante.',
        );
      } else {
        setError(err.message);
      }
    } finally {
      setDisconnecting(false);
    }
  }, []);

  function copyToClipboard(value: string, key: string) {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(key);
      setTimeout(() => setCopied(null), 2000);
    });
  }

  const badge = domainStatus ? statusBadge(domainStatus.status) : null;

  return (
    <div className="space-y-8">
      {/* ── Setup form ── */}
      <div className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2">
          <div>
            <label className="block text-sm font-semibold text-on-surface">
              Dominio mittente
            </label>
            <p className="mt-0.5 text-xs text-on-surface-variant">
              Il sottodominio da cui usciranno le email, es.{' '}
              <span className="font-mono">mail.tuodominio.it</span>
            </p>
            <input
              type="text"
              value={domain}
              placeholder="mail.tuodominio.it"
              autoComplete="off"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              onChange={(e) => {
                setDomain(e.target.value);
                if (error) setError(null);
              }}
              className="mt-2 w-full rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-3 py-2 font-mono text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:ring-2 focus:ring-primary/60"
            />
            {domain.includes('@') && (
              <p className="mt-1 text-xs text-error">
                Sembra un&apos;email. Inserisci solo il dominio, es.{' '}
                <span className="font-mono">agenda-pro.it</span>
              </p>
            )}
          </div>
          <div>
            <label className="block text-sm font-semibold text-on-surface">
              Nome mittente
            </label>
            <p className="mt-0.5 text-xs text-on-surface-variant">
              Appare nell&apos;inbox, es.{' '}
              <span className="font-mono">Rossi Solar</span>
            </p>
            <input
              type="text"
              value={fromName}
              placeholder="La tua azienda"
              onChange={(e) => setFromName(e.target.value)}
              className="mt-2 w-full rounded-lg border border-outline-variant/40 bg-surface-container-lowest px-3 py-2 text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:ring-2 focus:ring-primary/60"
            />
          </div>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button"
            disabled={!domain.trim() || loading}
            onClick={handleSetup}
            className="rounded-lg bg-primary px-5 py-2 text-sm font-semibold text-on-primary transition-opacity disabled:opacity-40 hover:opacity-90"
          >
            {loading ? 'Connessione…' : domainStatus ? 'Aggiorna dominio' : 'Aggiungi dominio'}
          </button>

          {/* Show Ricontrolla whenever a domain is configured — even if the
              SSR status fetch failed (network timeout, cookie expiry, etc.)
              and domainStatus is null. The domain field itself is the source
              of truth: if it's non-empty we know a domain was registered. */}
          {(domainStatus || initialDomain) && (
            <button
              type="button"
              disabled={checking}
              onClick={handleCheck}
              className="rounded-lg border border-outline-variant/40 bg-surface-container px-4 py-2 text-sm font-semibold text-on-surface transition-colors hover:bg-surface-container-high disabled:opacity-40"
            >
              {checking ? 'Verifica…' : '↻ Ricontrolla'}
            </button>
          )}

          {(domainStatus || initialDomain) && (
            <button
              type="button"
              disabled={disconnecting}
              onClick={handleDisconnect}
              className="ml-auto rounded-lg border border-error/40 bg-surface-container-lowest px-4 py-2 text-sm font-semibold text-error transition-colors hover:bg-error-container/30 disabled:opacity-40"
            >
              {disconnecting ? 'Disconnessione…' : 'Disconnetti dominio'}
            </button>
          )}

          {badge && (
            <span
              className={cn(
                'rounded-full px-3 py-1 text-xs font-semibold',
                badge.cls,
              )}
            >
              {badge.label}
            </span>
          )}
        </div>

        {error && (
          <div className="rounded-lg border border-error/40 bg-error-container/30 px-3 py-2 text-sm text-on-error-container">
            <p>{error}</p>
            <p className="mt-2 text-xs opacity-80">
              Se il problema persiste, clicca{' '}
              <strong>Disconnetti dominio</strong> per resettare la
              configurazione e riprovare. L&apos;operazione è reversibile e non
              tocca i tuoi record DNS.
            </p>
          </div>
        )}
      </div>

      {/* ── DNS records table ── */}
      {domainStatus && domainStatus.dns_records.length > 0 && (
        <div>
          <p className="mb-3 text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Record DNS da configurare
          </p>
          <p className="mb-4 text-sm text-on-surface-variant">
            Inserisci questi record nel pannello DNS del tuo provider (Cloudflare,
            Aruba, Register.it, ecc.). La propagazione può richiedere da pochi
            minuti fino a 24&nbsp;h. Poi clicca{' '}
            <strong className="text-on-surface">Ricontrolla</strong> per aggiornare
            lo stato.
          </p>

          <div className="overflow-hidden rounded-xl border border-outline-variant/30">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-outline-variant/20 bg-surface-container">
                  <th className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                    Tipo
                  </th>
                  <th className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                    Nome
                  </th>
                  <th className="px-4 py-2.5 text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                    Valore
                  </th>
                  <th className="px-4 py-2.5 text-center text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                    Stato
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-outline-variant/10">
                {domainStatus.dns_records.map((rec, i) => {
                  const copyKey = `${i}-${rec.type}`;
                  return (
                    <tr key={i} className="bg-surface-container-lowest">
                      <td className="px-4 py-3">
                        <span className="rounded bg-surface-container px-2 py-0.5 font-mono text-xs font-bold text-on-surface">
                          {rec.type}
                        </span>
                      </td>
                      <td className="max-w-[180px] truncate px-4 py-3 font-mono text-xs text-on-surface">
                        {rec.name}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <span className="max-w-[280px] truncate font-mono text-xs text-on-surface">
                            {rec.value}
                          </span>
                          <button
                            type="button"
                            onClick={() => copyToClipboard(rec.value, copyKey)}
                            className="flex-shrink-0 rounded px-1.5 py-0.5 text-[10px] text-on-surface-variant hover:bg-surface-container hover:text-on-surface"
                          >
                            {copied === copyKey ? '✓' : 'Copia'}
                          </button>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span
                          className={cn(
                            'inline-block h-2 w-2 rounded-full',
                            recordStatusDot(rec.status),
                          )}
                          title={rec.status}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* SPF/DMARC guidance */}
          <div className="mt-4 grid gap-3 md:grid-cols-3">
            <GuidanceCard
              label="SPF"
              color="text-primary"
              text="Il record TXT SPF autorizza Resend a inviare per il tuo dominio. Obbligatorio."
            />
            <GuidanceCard
              label="DKIM"
              color="text-tertiary"
              text="I record CNAME DKIM firmano le email e aumentano il trust nei filtri antispam."
            />
            <GuidanceCard
              label="DMARC"
              color="text-on-surface-variant"
              text='Aggiungi anche: TXT su _dmarc.{dominio} con valore "v=DMARC1; p=none; rua=mailto:dmarc@tuodominio.it"'
            />
          </div>
        </div>
      )}

      {/* Empty state */}
      {!domainStatus && (
        <div className="rounded-xl border border-dashed border-outline-variant/40 px-6 py-10 text-center">
          <p className="text-sm font-semibold text-on-surface-variant">
            Nessun dominio configurato
          </p>
          <p className="mt-1 text-xs text-on-surface-variant">
            Inserisci il sottodominio sopra e clicca{' '}
            <strong>Aggiungi dominio</strong> per ricevere i record DNS.
          </p>
        </div>
      )}
    </div>
  );
}

function GuidanceCard({
  label,
  color,
  text,
}: {
  label: string;
  color: string;
  text: string;
}) {
  return (
    <div className="rounded-lg bg-surface-container-low p-3">
      <p className={cn('text-xs font-bold', color)}>{label}</p>
      <p className="mt-1 text-xs text-on-surface-variant">{text}</p>
    </div>
  );
}
