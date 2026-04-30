/**
 * Preventivo editor — converts a hot lead into a formal PDF quote.
 *
 * Layout:
 *   - Left  (sidebar):  read-only AUTO bag the API computed
 *                       (anagrafica, kWp, kWh/anno, ROI, render hero).
 *   - Right (form):     6 MANUAL sections the installer fills in
 *                       (commerciale, tech, prezzo, incentivi, pagamento,
 *                        tempi, note). On submit → POST /v1/leads/:id/quote
 *                        → toast + iframe preview of the rendered PDF.
 *
 * Why this is a client component (not server): the editor is heavily
 * interactive — auto-summed `tempi_totale_giorni`, computed `prezzo_finale`,
 * accumulo toggle that gates a sub-section, optimistic submit-state.
 * Doing all of that round-trip with server actions would feel laggy.
 *
 * Why plain useState (not react-hook-form): the form has ~25 fields but
 * the validation rules are trivial ("number, > 0" or "non-empty string").
 * RHF's API surface costs ~12 kB gzipped; for one page that's not worth it.
 * Pattern matches other dashboard editors (branding-editor, about-editor).
 */
'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowLeft, Check, FileText, Loader2, X } from 'lucide-react';
import Link from 'next/link';

import { api, ApiError } from '@/lib/api-client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface QuoteDraft {
  auto_fields: Record<string, unknown>;
  suggested_preventivo_number: string;
  suggested_preventivo_seq: number;
}

interface QuoteResponse {
  id: string;
  preventivo_number: string;
  version: number;
  pdf_url: string | null;
}

interface ManualForm {
  // Preventivo header
  preventivo_numero: string;
  preventivo_data: string;
  preventivo_validita: string;
  // Commerciale
  commerciale_nome: string;
  commerciale_ruolo: string;
  commerciale_email: string;
  commerciale_telefono: string;
  // Tech — pannelli
  tech_marca_pannelli: string;
  tech_modello_pannelli: string;
  tech_potenza_singolo_pannello: string;
  tech_garanzia_pannelli_anni: number;
  tech_garanzia_produzione_anni: number;
  // Tech — inverter
  tech_marca_inverter: string;
  tech_modello_inverter: string;
  tech_garanzia_inverter_anni: number;
  // Tech — struttura + accumulo
  tech_struttura_montaggio: string;
  tech_sistema_monitoraggio: string;
  tech_accumulo_incluso: boolean;
  tech_accumulo_kwh: number;
  tech_accumulo_marca: string;
  // Prezzo
  prezzo_costo_impianto_lordo: number;
  prezzo_iva_inclusa: boolean;
  prezzo_aliquota_iva: number;
  prezzo_sconto_perc: number;
  prezzo_sconto_eur: number;
  // Incentivi
  incentivo_transizione_50_perc: number;
  incentivo_transizione_50_eur: number;
  incentivo_iva_agevolata: boolean;
  incentivo_super_ammortamento: number;
  incentivo_altri_descrizione: string;
  incentivo_altri_eur: number;
  // Pagamento
  pagamento_modalita_descrizione: string;
  pagamento_finanziamento_disponibile: boolean;
  pagamento_finanziamento_descrizione: string;
  // Tempi (giorni)
  tempi_progettazione_giorni: number;
  tempi_pratiche_giorni: number;
  tempi_installazione_giorni: number;
  tempi_collaudo_giorni: number;
  // Note
  note_aggiuntive: string;
}

// ---------------------------------------------------------------------------
// Defaults — sensible Italian B2B baseline. Installer overrides as needed.
// ---------------------------------------------------------------------------

const DEFAULT_FORM: ManualForm = {
  preventivo_numero: '',
  preventivo_data: new Date().toLocaleDateString('it-IT', {
    day: '2-digit',
    month: 'long',
    year: 'numeric',
  }),
  preventivo_validita: '60 giorni',
  commerciale_nome: '',
  commerciale_ruolo: 'Responsabile commerciale',
  commerciale_email: '',
  commerciale_telefono: '',
  tech_marca_pannelli: 'JA Solar',
  tech_modello_pannelli: 'JAM72D40 580W Bifacial',
  tech_potenza_singolo_pannello: '580 W',
  tech_garanzia_pannelli_anni: 25,
  tech_garanzia_produzione_anni: 30,
  tech_marca_inverter: 'Huawei SUN2000',
  tech_modello_inverter: '100KTL-M2',
  tech_garanzia_inverter_anni: 10,
  tech_struttura_montaggio: 'K2 SystemSpeedRail su lamiera grecata',
  tech_sistema_monitoraggio: 'Huawei FusionSolar Cloud',
  tech_accumulo_incluso: false,
  tech_accumulo_kwh: 0,
  tech_accumulo_marca: '',
  prezzo_costo_impianto_lordo: 0,
  prezzo_iva_inclusa: false,
  prezzo_aliquota_iva: 10,
  prezzo_sconto_perc: 0,
  prezzo_sconto_eur: 0,
  incentivo_transizione_50_perc: 40,
  incentivo_transizione_50_eur: 0,
  incentivo_iva_agevolata: true,
  incentivo_super_ammortamento: 130,
  incentivo_altri_descrizione: '',
  incentivo_altri_eur: 0,
  pagamento_modalita_descrizione: '30% alla firma, 60% inizio lavori, 10% a fine collaudo',
  pagamento_finanziamento_disponibile: false,
  pagamento_finanziamento_descrizione: 'Investimento 0: distribuito sul risparmio in bolletta',
  tempi_progettazione_giorni: 15,
  tempi_pratiche_giorni: 30,
  tempi_installazione_giorni: 20,
  tempi_collaudo_giorni: 10,
  note_aggiuntive: '',
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function QuoteEditorPage({ params }: { params: { id: string } }) {
  const router = useRouter();
  const leadId = params.id;

  const [draft, setDraft] = useState<QuoteDraft | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [form, setForm] = useState<ManualForm>(DEFAULT_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [savedQuote, setSavedQuote] = useState<QuoteResponse | null>(null);

  // Initial draft fetch — populates the AUTO sidebar + the suggested
  // preventivo number. Allocating the seq here (per the route) means
  // two concurrent editors get distinct numbers; the unused one is
  // simply never written.
  useEffect(() => {
    let cancelled = false;
    api
      .get<QuoteDraft>(`/v1/leads/${leadId}/quote/draft`)
      .then((res) => {
        if (cancelled) return;
        setDraft(res);
        setForm((f) => ({
          ...f,
          preventivo_numero: res.suggested_preventivo_number,
        }));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg =
          err instanceof ApiError
            ? err.message
            : 'Impossibile caricare i dati del preventivo. Riprova.';
        setDraftError(msg);
      });
    return () => {
      cancelled = true;
    };
  }, [leadId]);

  // ----- Derived (computed from form on every render) ----------------------
  const prezzoFinale = Math.max(
    0,
    (form.prezzo_costo_impianto_lordo || 0) - (form.prezzo_sconto_eur || 0),
  );
  const tempiTotale =
    (form.tempi_progettazione_giorni || 0) +
    (form.tempi_pratiche_giorni || 0) +
    (form.tempi_installazione_giorni || 0) +
    (form.tempi_collaudo_giorni || 0);
  const incentivoTotale =
    (form.incentivo_transizione_50_eur || 0) + (form.incentivo_altri_eur || 0);
  const costoNetto = Math.max(0, prezzoFinale - incentivoTotale);

  // ----- Submit ------------------------------------------------------------
  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);

    // The renderer expects derived numeric values too — we POST them so
    // the server doesn't have to recompute. Keeps the manual_fields bag
    // self-contained and makes future re-render reproducible from the
    // stored row alone.
    const manualFields = {
      ...form,
      prezzo_finale: prezzoFinale,
      incentivo_totale_eur: incentivoTotale,
      costo_netto_post_incentivi: costoNetto,
      tempi_totale_giorni: tempiTotale,
    };

    try {
      const res = await api.post<QuoteResponse>(
        `/v1/leads/${leadId}/quote`,
        { manual_fields: manualFields },
      );
      setSavedQuote(res);
      // Refresh the underlying lead page so the version dropdown
      // is fresh next time the user navigates back.
      setTimeout(() => router.refresh(), 500);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.message
          : 'Errore durante la generazione del preventivo. Riprova.';
      setSubmitError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  // ----- Render ------------------------------------------------------------
  if (draftError) {
    return (
      <div className="space-y-4">
        <BackLink leadId={leadId} />
        <div className="rounded-lg border border-error bg-error-container/30 px-4 py-3 text-sm text-on-error-container">
          {draftError}
        </div>
      </div>
    );
  }

  if (!draft) {
    return (
      <div className="flex items-center gap-2 py-12 text-sm text-on-surface-variant">
        <Loader2 className="animate-spin" size={16} aria-hidden />
        Caricamento dati preventivo…
      </div>
    );
  }

  const auto = draft.auto_fields;

  return (
    <div className="space-y-4">
      <BackLink leadId={leadId} />
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="font-headline text-3xl font-bold tracking-tighter">
          Genera preventivo
        </h1>
        <span className="text-xs text-on-surface-variant">
          Numero proposto:{' '}
          <span className="font-headline font-bold text-primary">
            {draft.suggested_preventivo_number}
          </span>
        </span>
      </header>

      <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
        {/* ─── AUTO sidebar (read-only) ───────────────────────────── */}
        <aside className="space-y-3 rounded-xl border border-outline-variant bg-surface-container px-5 py-4 lg:sticky lg:top-4 lg:self-start">
          <h2 className="text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
            Dati automatici
          </h2>

          {auto.render_after_url && typeof auto.render_after_url === 'string' ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={auto.render_after_url}
              alt="Render impianto"
              className="aspect-square w-full rounded-lg object-cover"
            />
          ) : null}

          <SidebarRow label="Cliente" value={String(auto.azienda_ragione_sociale || '—')} />
          <SidebarRow label="Decisore" value={String(auto.azienda_decisore_nome || '—')} />
          <SidebarRow
            label="Sede operativa"
            value={String(auto.azienda_sede_operativa || '—')}
          />
          <Divider />
          <SidebarRow label="kWp installabili" value={`${auto.solar_kw_installabili || '—'} kWp`} />
          <SidebarRow label="kWh/anno stimati" value={`${auto.solar_kwh_annui || '—'} kWh`} />
          <SidebarRow label="N° pannelli" value={String(auto.solar_pannelli_numero || '—')} />
          <Divider />
          <SidebarRow
            label="Risparmio anno 1"
            value={`€ ${auto.econ_risparmio_anno_1 || '—'}`}
          />
          <SidebarRow
            label="Risparmio 25 anni"
            value={`€ ${auto.econ_risparmio_25_anni || '—'}`}
          />
          <SidebarRow label="Payback" value={`${auto.econ_payback_anni || '—'} anni`} />
          <SidebarRow label="ROI 25 anni" value={`${auto.econ_irr_25_anni || '—'} %`} />
        </aside>

        {/* ─── MANUAL form ─────────────────────────────────────────── */}
        <form onSubmit={onSubmit} className="space-y-4">
          <Section title="Anagrafica preventivo">
            <Field label="Numero preventivo">
              <Input
                value={form.preventivo_numero}
                onChange={(v) => setForm({ ...form, preventivo_numero: v })}
              />
            </Field>
            <Field label="Data">
              <Input
                value={form.preventivo_data}
                onChange={(v) => setForm({ ...form, preventivo_data: v })}
              />
            </Field>
            <Field label="Validità">
              <Input
                value={form.preventivo_validita}
                onChange={(v) => setForm({ ...form, preventivo_validita: v })}
              />
            </Field>
          </Section>

          <Section title="Riferimento commerciale">
            <Field label="Nome">
              <Input
                value={form.commerciale_nome}
                onChange={(v) => setForm({ ...form, commerciale_nome: v })}
              />
            </Field>
            <Field label="Ruolo">
              <Input
                value={form.commerciale_ruolo}
                onChange={(v) => setForm({ ...form, commerciale_ruolo: v })}
              />
            </Field>
            <Field label="Email">
              <Input
                type="email"
                value={form.commerciale_email}
                onChange={(v) => setForm({ ...form, commerciale_email: v })}
              />
            </Field>
            <Field label="Telefono">
              <Input
                value={form.commerciale_telefono}
                onChange={(v) => setForm({ ...form, commerciale_telefono: v })}
              />
            </Field>
          </Section>

          <Section title="Configurazione tecnica">
            <Field label="Marca pannelli">
              <Input
                value={form.tech_marca_pannelli}
                onChange={(v) => setForm({ ...form, tech_marca_pannelli: v })}
              />
            </Field>
            <Field label="Modello pannelli">
              <Input
                value={form.tech_modello_pannelli}
                onChange={(v) => setForm({ ...form, tech_modello_pannelli: v })}
              />
            </Field>
            <Field label="Potenza singolo pannello">
              <Input
                value={form.tech_potenza_singolo_pannello}
                onChange={(v) =>
                  setForm({ ...form, tech_potenza_singolo_pannello: v })
                }
              />
            </Field>
            <Field label="Marca inverter">
              <Input
                value={form.tech_marca_inverter}
                onChange={(v) => setForm({ ...form, tech_marca_inverter: v })}
              />
            </Field>
            <Field label="Modello inverter">
              <Input
                value={form.tech_modello_inverter}
                onChange={(v) => setForm({ ...form, tech_modello_inverter: v })}
              />
            </Field>
            <Field label="Struttura montaggio">
              <Input
                value={form.tech_struttura_montaggio}
                onChange={(v) =>
                  setForm({ ...form, tech_struttura_montaggio: v })
                }
              />
            </Field>
            <Field label="Accumulo incluso?">
              <Toggle
                checked={form.tech_accumulo_incluso}
                onChange={(c) => setForm({ ...form, tech_accumulo_incluso: c })}
              />
            </Field>
            {form.tech_accumulo_incluso && (
              <>
                <Field label="Capacità accumulo (kWh)">
                  <NumberInput
                    value={form.tech_accumulo_kwh}
                    onChange={(v) => setForm({ ...form, tech_accumulo_kwh: v })}
                  />
                </Field>
                <Field label="Marca / modello accumulo">
                  <Input
                    value={form.tech_accumulo_marca}
                    onChange={(v) => setForm({ ...form, tech_accumulo_marca: v })}
                  />
                </Field>
              </>
            )}
          </Section>

          <Section title="Prezzo">
            <Field label="Costo lordo chiavi in mano (€)">
              <NumberInput
                value={form.prezzo_costo_impianto_lordo}
                onChange={(v) =>
                  setForm({ ...form, prezzo_costo_impianto_lordo: v })
                }
              />
            </Field>
            <Field label="Sconto (%)">
              <NumberInput
                value={form.prezzo_sconto_perc}
                onChange={(v) => setForm({ ...form, prezzo_sconto_perc: v })}
              />
            </Field>
            <Field label="Sconto (€)">
              <NumberInput
                value={form.prezzo_sconto_eur}
                onChange={(v) => setForm({ ...form, prezzo_sconto_eur: v })}
              />
            </Field>
            <Field label="IVA inclusa nel prezzo?">
              <Toggle
                checked={form.prezzo_iva_inclusa}
                onChange={(c) => setForm({ ...form, prezzo_iva_inclusa: c })}
              />
            </Field>
            <Field label="Aliquota IVA (%)">
              <NumberInput
                value={form.prezzo_aliquota_iva}
                onChange={(v) => setForm({ ...form, prezzo_aliquota_iva: v })}
              />
            </Field>
            <Computed
              label="Prezzo finale"
              value={`€ ${prezzoFinale.toLocaleString('it-IT')}`}
            />
          </Section>

          <Section title="Incentivi e agevolazioni">
            <Field label="Transizione 5.0 (%)">
              <NumberInput
                value={form.incentivo_transizione_50_perc}
                onChange={(v) =>
                  setForm({ ...form, incentivo_transizione_50_perc: v })
                }
              />
            </Field>
            <Field label="Transizione 5.0 (€)">
              <NumberInput
                value={form.incentivo_transizione_50_eur}
                onChange={(v) =>
                  setForm({ ...form, incentivo_transizione_50_eur: v })
                }
              />
            </Field>
            <Field label="Super ammortamento (%)">
              <NumberInput
                value={form.incentivo_super_ammortamento}
                onChange={(v) =>
                  setForm({ ...form, incentivo_super_ammortamento: v })
                }
              />
            </Field>
            <Field label="Altri incentivi (descrizione)" wide>
              <Input
                value={form.incentivo_altri_descrizione}
                onChange={(v) =>
                  setForm({ ...form, incentivo_altri_descrizione: v })
                }
              />
            </Field>
            <Field label="Altri incentivi (€)">
              <NumberInput
                value={form.incentivo_altri_eur}
                onChange={(v) => setForm({ ...form, incentivo_altri_eur: v })}
              />
            </Field>
            <Computed
              label="Totale incentivi"
              value={`€ ${incentivoTotale.toLocaleString('it-IT')}`}
            />
            <Computed
              label="Costo netto post-incentivi"
              value={`€ ${costoNetto.toLocaleString('it-IT')}`}
            />
          </Section>

          <Section title="Modalità di pagamento">
            <Field label="Modalità (descrizione libera)" wide>
              <Textarea
                rows={2}
                value={form.pagamento_modalita_descrizione}
                onChange={(v) =>
                  setForm({ ...form, pagamento_modalita_descrizione: v })
                }
              />
            </Field>
            <Field label="Finanziamento disponibile?">
              <Toggle
                checked={form.pagamento_finanziamento_disponibile}
                onChange={(c) =>
                  setForm({
                    ...form,
                    pagamento_finanziamento_disponibile: c,
                  })
                }
              />
            </Field>
            {form.pagamento_finanziamento_disponibile && (
              <Field label="Descrizione finanziamento" wide>
                <Textarea
                  rows={2}
                  value={form.pagamento_finanziamento_descrizione}
                  onChange={(v) =>
                    setForm({
                      ...form,
                      pagamento_finanziamento_descrizione: v,
                    })
                  }
                />
              </Field>
            )}
          </Section>

          <Section title="Tempi di realizzazione (giorni)">
            <Field label="Progettazione esecutiva">
              <NumberInput
                value={form.tempi_progettazione_giorni}
                onChange={(v) =>
                  setForm({ ...form, tempi_progettazione_giorni: v })
                }
              />
            </Field>
            <Field label="Pratiche autorizzative">
              <NumberInput
                value={form.tempi_pratiche_giorni}
                onChange={(v) => setForm({ ...form, tempi_pratiche_giorni: v })}
              />
            </Field>
            <Field label="Installazione">
              <NumberInput
                value={form.tempi_installazione_giorni}
                onChange={(v) =>
                  setForm({ ...form, tempi_installazione_giorni: v })
                }
              />
            </Field>
            <Field label="Collaudo">
              <NumberInput
                value={form.tempi_collaudo_giorni}
                onChange={(v) => setForm({ ...form, tempi_collaudo_giorni: v })}
              />
            </Field>
            <Computed label="Totale" value={`${tempiTotale} giorni`} />
          </Section>

          <Section title="Note aggiuntive">
            <Field label="" wide>
              <Textarea
                rows={4}
                value={form.note_aggiuntive}
                onChange={(v) => setForm({ ...form, note_aggiuntive: v })}
              />
            </Field>
          </Section>

          {/* Submit footer */}
          <div className="flex items-center justify-end gap-3 pt-4">
            {submitError && (
              <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-error">
                <X size={12} strokeWidth={2.5} aria-hidden /> {submitError}
              </span>
            )}
            <button
              type="submit"
              disabled={submitting}
              className="inline-flex items-center gap-2 rounded-full bg-gradient-primary px-6 py-3 text-sm font-bold text-on-primary shadow-ambient-sm transition-all hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting ? (
                <>
                  <Loader2 className="animate-spin" size={14} aria-hidden />
                  Generazione PDF in corso…
                </>
              ) : (
                <>
                  <FileText size={14} strokeWidth={2.25} aria-hidden /> Salva e
                  genera PDF
                </>
              )}
            </button>
          </div>
        </form>
      </div>

      {/* ─── Preview pane (visible after first save) ───────────────── */}
      {savedQuote && savedQuote.pdf_url && (
        <section className="space-y-2 rounded-xl border border-primary/20 bg-primary-container/20 p-4">
          <p className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary">
            <Check size={14} strokeWidth={2.5} aria-hidden /> Preventivo{' '}
            {savedQuote.preventivo_number} v{savedQuote.version} generato.
          </p>
          <iframe
            src={savedQuote.pdf_url}
            className="h-[800px] w-full rounded-lg border border-outline-variant bg-white"
            title={`Preventivo ${savedQuote.preventivo_number}`}
          />
        </section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tiny presentational primitives (kept inline so the file is self-contained)
// ---------------------------------------------------------------------------

function BackLink({ leadId }: { leadId: string }) {
  return (
    <Link
      href={`/leads/${leadId}`}
      className="inline-flex items-center gap-1 text-xs font-medium text-on-surface-variant transition-colors hover:text-primary"
    >
      <ArrowLeft size={12} strokeWidth={2.25} aria-hidden /> Torna al lead
    </Link>
  );
}

function SidebarRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2 text-xs">
      <span className="text-on-surface-variant">{label}</span>
      <span className="font-headline font-semibold text-on-surface">{value}</span>
    </div>
  );
}

function Divider() {
  return <hr className="border-t border-outline-variant" />;
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <fieldset className="space-y-3 rounded-xl border border-outline-variant bg-surface-container px-5 py-4">
      <legend className="px-2 text-xs font-semibold uppercase tracking-widest text-on-surface-variant">
        {title}
      </legend>
      <div className="grid gap-3 sm:grid-cols-2">{children}</div>
    </fieldset>
  );
}

function Field({
  label,
  children,
  wide,
}: {
  label: string;
  children: React.ReactNode;
  wide?: boolean;
}) {
  return (
    <label className={`flex flex-col gap-1 text-xs ${wide ? 'sm:col-span-2' : ''}`}>
      {label && <span className="text-on-surface-variant">{label}</span>}
      {children}
    </label>
  );
}

function Input({
  value,
  onChange,
  type = 'text',
}: {
  value: string;
  onChange: (v: string) => void;
  type?: string;
}) {
  return (
    <input
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-md border border-outline-variant bg-surface px-3 py-2 text-sm text-on-surface focus:border-primary focus:outline-none"
    />
  );
}

function NumberInput({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <input
      type="number"
      value={value}
      onChange={(e) => {
        const n = Number(e.target.value);
        onChange(Number.isFinite(n) ? n : 0);
      }}
      className="rounded-md border border-outline-variant bg-surface px-3 py-2 text-sm text-on-surface focus:border-primary focus:outline-none"
    />
  );
}

function Textarea({
  value,
  onChange,
  rows = 3,
}: {
  value: string;
  onChange: (v: string) => void;
  rows?: number;
}) {
  return (
    <textarea
      value={value}
      rows={rows}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-md border border-outline-variant bg-surface px-3 py-2 text-sm text-on-surface focus:border-primary focus:outline-none"
    />
  );
}

function Toggle({
  checked,
  onChange,
}: {
  checked: boolean;
  onChange: (c: boolean) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onChange(!checked)}
      className={`inline-flex h-7 w-12 items-center rounded-full transition-colors ${
        checked ? 'bg-primary' : 'bg-surface-container-highest'
      }`}
      aria-pressed={checked}
    >
      <span
        className={`h-5 w-5 rounded-full bg-white shadow-sm transition-transform ${
          checked ? 'translate-x-6' : 'translate-x-1'
        }`}
      />
    </button>
  );
}

function Computed({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md bg-primary-container/20 px-3 py-2 text-xs sm:col-span-2">
      <span className="text-on-surface-variant">{label}: </span>
      <span className="font-headline font-bold text-primary">{value}</span>
    </div>
  );
}
