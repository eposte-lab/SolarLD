'use client';

/**
 * SectorNewsPageClient — Sprint 10.
 *
 * Owns:
 *   - Listing (own rows + global seeds, sorted by status/ateco)
 *   - Create modal (tenant-scoped row, form-validated)
 *   - Edit modal (tenant-owned only — global rows are read-only)
 *   - Soft-archive (DELETE → status='archived')
 *
 * The dashboard talks to FastAPI ``/v1/sector-news/*`` (RLS-scoped).
 * Global rows have ``tenant_id === null`` and the UI shows a lock badge.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowUpRight, Lock, Pencil, Plus, Trash2 } from 'lucide-react';

import { BentoCard } from '@/components/ui/bento-card';
import { GradientButton } from '@/components/ui/gradient-button';
import {
  archiveSectorNews,
  createSectorNews,
  listSectorNews,
  updateSectorNews,
  type SectorNews,
} from '@/lib/data/sector-news';

interface FormState {
  ateco_2digit: string;
  headline: string;
  body: string;
  source_url: string;
  status: 'active' | 'archived';
}

const EMPTY_FORM: FormState = {
  ateco_2digit: '',
  headline: '',
  body: '',
  source_url: '',
  status: 'active',
};

export function SectorNewsPageClient({ tenantId }: { tenantId: string }) {
  const [rows, setRows] = useState<SectorNews[]>([]);
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [editing, setEditing] = useState<SectorNews | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      const res = await listSectorNews();
      setRows(res.rows);
    } catch (err) {
      setErrorMsg(
        err instanceof Error ? err.message : 'Caricamento fallito',
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const ownRows = useMemo(
    () => rows.filter((r) => r.tenant_id === tenantId),
    [rows, tenantId],
  );
  const globalRows = useMemo(
    () => rows.filter((r) => r.tenant_id === null),
    [rows],
  );

  const handleArchive = useCallback(
    async (row: SectorNews) => {
      if (row.tenant_id === null) return; // safety: global rows are read-only
      const ok = window.confirm(
        `Archiviare "${row.headline}"? Verrà nascosta dal motore di follow-up.`,
      );
      if (!ok) return;
      try {
        await archiveSectorNews(row.id);
        await load();
      } catch (err) {
        setErrorMsg(
          err instanceof Error ? err.message : 'Archiviazione fallita',
        );
      }
    },
    [load],
  );

  return (
    <div className="space-y-6">
      {errorMsg && (
        <div className="rounded-lg border border-error/40 bg-error-container/30 px-4 py-3 text-sm text-on-error-container">
          {errorMsg}
        </div>
      )}

      <BentoCard span="full">
        <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
          <div>
            <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
              Le tue news ({ownRows.length})
            </p>
            <h2 className="mt-1 font-headline text-2xl font-bold tracking-tighter">
              Catalogo personalizzato
            </h2>
            <p className="mt-1 max-w-xl text-sm text-on-surface-variant">
              Le righe qui sotto sostituiscono le globali quando coincide il
              codice ATECO 2-digit. Il motore di follow-up sceglie sempre la
              tua, se esiste.
            </p>
          </div>
          <GradientButton
            variant="primary"
            size="md"
            onClick={() => setCreating(true)}
          >
            <span className="inline-flex items-center gap-2">
              <Plus size={14} strokeWidth={2.25} aria-hidden />
              Nuova news
            </span>
          </GradientButton>
        </div>

        <div className="mt-6">
          {loading ? (
            <p className="text-sm text-on-surface-variant">Caricamento…</p>
          ) : ownRows.length === 0 ? (
            <p className="text-sm text-on-surface-variant">
              Non hai ancora creato news. Le globali sotto vengono usate di
              default — clicca <em>Nuova news</em> per sovrascriverne una con
              testo specifico per il tuo brand.
            </p>
          ) : (
            <NewsTable
              rows={ownRows}
              onEdit={setEditing}
              onArchive={handleArchive}
              showActions
            />
          )}
        </div>
      </BentoCard>

      <BentoCard span="full">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          News globali ({globalRows.length})
        </p>
        <h2 className="mt-1 font-headline text-2xl font-bold tracking-tighter">
          Seed precaricati da SolarLead
        </h2>
        <p className="mt-1 max-w-xl text-sm text-on-surface-variant">
          Read-only. Per personalizzarle, crea una tua riga con lo stesso
          codice ATECO — la tua avrà la precedenza.
        </p>
        <div className="mt-6">
          {globalRows.length === 0 ? (
            <p className="text-sm text-on-surface-variant">
              Nessun seed globale disponibile.
            </p>
          ) : (
            <NewsTable
              rows={globalRows}
              onEdit={() => undefined}
              onArchive={() => undefined}
              showActions={false}
            />
          )}
        </div>
      </BentoCard>

      {(creating || editing) && (
        <NewsFormDialog
          initial={editing ?? null}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSaved={async () => {
            setCreating(false);
            setEditing(null);
            await load();
          }}
          onError={setErrorMsg}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Table
// ---------------------------------------------------------------------------

function NewsTable({
  rows,
  onEdit,
  onArchive,
  showActions,
}: {
  rows: SectorNews[];
  onEdit: (row: SectorNews) => void;
  onArchive: (row: SectorNews) => void;
  showActions: boolean;
}) {
  return (
    <div className="overflow-hidden rounded-lg bg-surface-container-lowest">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
            <th className="px-4 py-3 w-16">ATECO</th>
            <th className="px-4 py-3">Titolo</th>
            <th className="px-4 py-3">Stato</th>
            {showActions && <th className="px-4 py-3 w-28 text-right">Azioni</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr
              key={row.id}
              style={
                idx !== 0
                  ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }
                  : undefined
              }
            >
              <td className="px-4 py-3 font-mono text-xs text-on-surface">
                {row.ateco_2digit}
              </td>
              <td className="px-4 py-3">
                <p className="font-medium text-on-surface">{row.headline}</p>
                <p className="mt-0.5 line-clamp-2 text-xs text-on-surface-variant">
                  {row.body}
                </p>
                {row.source_url && (
                  <a
                    href={row.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-1 inline-flex items-center gap-1 text-xs text-primary hover:underline"
                  >
                    Fonte
                    <ArrowUpRight size={12} strokeWidth={2} aria-hidden />
                  </a>
                )}
              </td>
              <td className="px-4 py-3">
                <StatusBadge status={row.status} isGlobal={row.tenant_id === null} />
              </td>
              {showActions && (
                <td className="px-4 py-3 text-right">
                  <div className="inline-flex items-center gap-1.5">
                    <button
                      type="button"
                      aria-label="Modifica"
                      title="Modifica"
                      onClick={() => onEdit(row)}
                      className="rounded-md p-1.5 text-on-surface-variant hover:bg-surface-container-high hover:text-on-surface"
                    >
                      <Pencil size={14} strokeWidth={2} aria-hidden />
                    </button>
                    {row.status === 'active' && (
                      <button
                        type="button"
                        aria-label="Archivia"
                        title="Archivia"
                        onClick={() => onArchive(row)}
                        className="rounded-md p-1.5 text-on-surface-variant hover:bg-error-container/40 hover:text-error"
                      >
                        <Trash2 size={14} strokeWidth={2} aria-hidden />
                      </button>
                    )}
                  </div>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StatusBadge({
  status,
  isGlobal,
}: {
  status: 'active' | 'archived';
  isGlobal: boolean;
}) {
  if (isGlobal) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-outline-variant/60 bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
        <Lock size={10} strokeWidth={2.5} aria-hidden />
        Globale
      </span>
    );
  }
  if (status === 'archived') {
    return (
      <span className="rounded-full bg-surface-container-high px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-surface-variant">
        Archiviata
      </span>
    );
  }
  return (
    <span className="rounded-full bg-primary-container px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-on-primary-container">
      Attiva
    </span>
  );
}

// ---------------------------------------------------------------------------
// Create / edit dialog
// ---------------------------------------------------------------------------

function NewsFormDialog({
  initial,
  onClose,
  onSaved,
  onError,
}: {
  initial: SectorNews | null;
  onClose: () => void;
  onSaved: () => Promise<void> | void;
  onError: (msg: string) => void;
}) {
  const [form, setForm] = useState<FormState>(
    initial
      ? {
          ateco_2digit: initial.ateco_2digit,
          headline: initial.headline,
          body: initial.body,
          source_url: initial.source_url ?? '',
          status: initial.status,
        }
      : EMPTY_FORM,
  );
  const [saving, setSaving] = useState(false);

  const isEdit = initial !== null;

  const validate = (): string | null => {
    if (!/^\d{2}$/.test(form.ateco_2digit)) {
      return 'Il codice ATECO deve essere di 2 cifre (es. "25", "41").';
    }
    if (form.headline.trim().length < 10 || form.headline.trim().length > 140) {
      return 'Il titolo deve essere tra 10 e 140 caratteri.';
    }
    if (form.body.trim().length < 20 || form.body.trim().length > 600) {
      return 'Il corpo deve essere tra 20 e 600 caratteri.';
    }
    if (form.source_url && !/^https?:\/\//.test(form.source_url)) {
      return 'La fonte deve iniziare con http:// o https://.';
    }
    return null;
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const err = validate();
    if (err) {
      onError(err);
      return;
    }
    setSaving(true);
    try {
      const payload = {
        ateco_2digit: form.ateco_2digit,
        headline: form.headline.trim(),
        body: form.body.trim(),
        source_url: form.source_url.trim() || null,
      };
      if (isEdit && initial) {
        await updateSectorNews(initial.id, {
          ...payload,
          status: form.status,
        });
      } else {
        await createSectorNews(payload);
      }
      await onSaved();
    } catch (e2) {
      onError(e2 instanceof Error ? e2.message : 'Salvataggio fallito');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
    >
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-2xl space-y-5 rounded-2xl bg-surface p-6 shadow-2xl"
      >
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            {isEdit ? 'Modifica news' : 'Nuova news di settore'}
          </p>
          <h2 className="mt-1 font-headline text-2xl font-bold tracking-tighter">
            {isEdit ? form.headline.slice(0, 60) : 'Aggancio non-creepy'}
          </h2>
          <p className="mt-1 text-xs text-on-surface-variant">
            Il motore cita questo titolo + corpo nelle email follow-up agli
            interessati. Niente "abbiamo visto che hai aperto" — solo fatti di
            settore.
          </p>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
          <div className="md:col-span-1">
            <label
              htmlFor="ateco"
              className="block text-xs font-semibold text-on-surface"
            >
              ATECO 2-digit
            </label>
            <input
              id="ateco"
              type="text"
              required
              maxLength={2}
              pattern="\d{2}"
              value={form.ateco_2digit}
              onChange={(e) =>
                setForm((f) => ({ ...f, ateco_2digit: e.target.value }))
              }
              className="mt-1 w-full rounded-lg border border-outline/40 bg-surface-container-lowest px-3 py-2 font-mono text-sm focus:border-primary focus:outline-none"
              placeholder="25"
            />
            <p className="mt-1 text-[10px] text-on-surface-variant">
              Es. "25" = metalmeccanico, "41" = costruzioni
            </p>
          </div>

          {isEdit && (
            <div className="md:col-span-2">
              <label
                htmlFor="status"
                className="block text-xs font-semibold text-on-surface"
              >
                Stato
              </label>
              <select
                id="status"
                value={form.status}
                onChange={(e) =>
                  setForm((f) => ({
                    ...f,
                    status: e.target.value as 'active' | 'archived',
                  }))
                }
                className="mt-1 w-full rounded-lg border border-outline/40 bg-surface-container-lowest px-3 py-2 text-sm focus:border-primary focus:outline-none"
              >
                <option value="active">Attiva</option>
                <option value="archived">Archiviata</option>
              </select>
              <p className="mt-1 text-[10px] text-on-surface-variant">
                Le archiviate non vengono mai citate nelle email.
              </p>
            </div>
          )}
        </div>

        <div>
          <label
            htmlFor="headline"
            className="block text-xs font-semibold text-on-surface"
          >
            Titolo (10-140 caratteri)
          </label>
          <input
            id="headline"
            type="text"
            required
            minLength={10}
            maxLength={140}
            value={form.headline}
            onChange={(e) =>
              setForm((f) => ({ ...f, headline: e.target.value }))
            }
            className="mt-1 w-full rounded-lg border border-outline/40 bg-surface-container-lowest px-3 py-2 text-sm focus:border-primary focus:outline-none"
            placeholder="Credito d'imposta 6.0 prorogato per il metalmeccanico"
          />
          <p className="mt-1 text-right text-[10px] text-on-surface-variant">
            {form.headline.length}/140
          </p>
        </div>

        <div>
          <label
            htmlFor="body"
            className="block text-xs font-semibold text-on-surface"
          >
            Corpo (20-600 caratteri)
          </label>
          <textarea
            id="body"
            required
            minLength={20}
            maxLength={600}
            rows={5}
            value={form.body}
            onChange={(e) =>
              setForm((f) => ({ ...f, body: e.target.value }))
            }
            className="mt-1 w-full rounded-lg border border-outline/40 bg-surface-container-lowest px-3 py-2 text-sm focus:border-primary focus:outline-none"
            placeholder="2-3 frasi di contesto. Cita un dato concreto (percentuale, scadenza, importo). Niente riferimenti al comportamento del lead."
          />
          <p className="mt-1 text-right text-[10px] text-on-surface-variant">
            {form.body.length}/600
          </p>
        </div>

        <div>
          <label
            htmlFor="source_url"
            className="block text-xs font-semibold text-on-surface"
          >
            Fonte (URL, opzionale)
          </label>
          <input
            id="source_url"
            type="url"
            value={form.source_url}
            onChange={(e) =>
              setForm((f) => ({ ...f, source_url: e.target.value }))
            }
            className="mt-1 w-full rounded-lg border border-outline/40 bg-surface-container-lowest px-3 py-2 font-mono text-xs focus:border-primary focus:outline-none"
            placeholder="https://www.mise.gov.it/..."
          />
          <p className="mt-1 text-[10px] text-on-surface-variant">
            Se valorizzata, viene linkata in fondo al messaggio "Fonte: …".
          </p>
        </div>

        <div className="flex items-center justify-end gap-3 border-t border-outline/20 pt-4">
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="rounded-lg px-4 py-2 text-sm font-medium text-on-surface-variant hover:bg-surface-container-high"
          >
            Annulla
          </button>
          <GradientButton
            variant="primary"
            size="md"
            type="submit"
            disabled={saving}
          >
            {saving ? 'Salvataggio…' : isEdit ? 'Salva modifiche' : 'Crea news'}
          </GradientButton>
        </div>
      </form>
    </div>
  );
}
