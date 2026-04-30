/**
 * Crea pratica GSE — form post-firma per generare 2 PDF (DM 37/08 +
 * Comunicazione Comune) in un solo submit.
 *
 * Flusso:
 *   1. Mount → GET /v1/leads/{id}/practice/draft → prefill form +
 *      eligibility flags + suggested_practice_number.
 *   2. Submit → POST /v1/leads/{id}/practice → 201 con practice_id.
 *      Redirect a /practices/{practice_id} (la pagina detail polla i
 *      documenti finché il worker non li riempie).
 *   3. Edge case: 409 (pratica già esistente) → redirect su quella.
 *   4. Banner warning se `missing_tenant_fields`: si può comunque
 *      creare la pratica, ma la generazione del DM 37/08 fallirà
 *      finché il tenant non compila i Dati legali. La Comunicazione
 *      Comune funziona indipendentemente.
 *
 * useState plain (no react-hook-form) — coerente con il quote editor.
 */
'use client';

import { use, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import Link from 'next/link';
import { ArrowLeft, FileText, Loader2, AlertTriangle } from 'lucide-react';

import { api, ApiError } from '@/lib/api-client';

// ---------------------------------------------------------------------------
// Types — slim mirrors of the FastAPI shapes
// ---------------------------------------------------------------------------

interface DraftPreview {
  eligible: boolean;
  has_existing: boolean;
  existing_practice_id: string | null;
  existing_practice_number: string | null;
  missing_tenant_fields: string[];
  suggested_practice_number: string;
  prefill: {
    impianto_potenza_kw?: number;
    impianto_pannelli_count?: number | null;
    impianto_distributore?: string;
    componenti?: {
      pannelli?: {
        marca?: string | null;
        modello?: string | null;
        potenza_w?: string | null;
        garanzia_anni?: number | null;
      };
      inverter?: {
        marca?: string | null;
        modello?: string | null;
        garanzia_anni?: number | null;
      };
      accumulo?: { presente?: boolean };
    };
    ubicazione?: {
      indirizzo?: string;
      cap?: string;
      comune?: string;
      provincia?: string;
    };
  };
  quote_id: string | null;
}

interface CreatePracticeResponse {
  practice: { id: string; practice_number: string };
  documents: Array<{ id: string; template_code: string }>;
}

interface FormState {
  // Impianto
  impianto_potenza_kw: number;
  impianto_pannelli_count: number;
  impianto_pod: string;
  impianto_distributore: string;
  impianto_data_inizio_lavori: string;
  impianto_data_fine_lavori: string;
  // Catastale
  catastale_foglio: string;
  catastale_particella: string;
  catastale_subalterno: string;
  // Componenti
  pann_marca: string;
  pann_modello: string;
  pann_potenza_w: string;
  pann_quantita: number;
  inv_marca: string;
  inv_modello: string;
  inv_potenza_kw: string;
  inv_quantita: number;
  acc_presente: boolean;
  acc_marca: string;
  acc_capacita_kwh: number;
  // Templates — Sprint 1
  gen_dm_37_08: boolean;
  gen_comunicazione_comune: boolean;
  // Templates — Sprint 2 (Modello Unico, TICA, schema, Transizione 5.0)
  gen_modello_unico_p1: boolean;
  gen_modello_unico_p2: boolean;
  gen_schema_unifilare: boolean;
  gen_attestazione_titolo: boolean;
  gen_tica_areti: boolean;
  gen_transizione_50_ex_ante: boolean;
  gen_transizione_50_ex_post: boolean;
  gen_transizione_50_attestazione: boolean;
  // Extras (Sprint 2 — JSONB stored on practices.extras)
  ex_iban: string;
  ex_regime_ritiro: string;
  ex_qualita_richiedente: string;
  ex_tipologia_struttura: string;
  ex_denominazione_impianto: string;
  ex_codice_identificativo_connessione: string;
  ex_codice_rintracciabilita: string;
  ex_potenza_immissione_kw: number;
  ex_configurazione_accumulo: string;
  // Transizione 5.0 sub-section
  ex_t50_ateco: string;
  ex_t50_tep_anno: number;
  ex_t50_percentuale_riduzione: number;
  ex_t50_fascia_agevolativa: string;
  ex_t50_investimento_totale_eur: number;
}

const DEFAULT_FORM: FormState = {
  impianto_potenza_kw: 0,
  impianto_pannelli_count: 0,
  impianto_pod: '',
  impianto_distributore: 'e_distribuzione',
  impianto_data_inizio_lavori: '',
  impianto_data_fine_lavori: '',
  catastale_foglio: '',
  catastale_particella: '',
  catastale_subalterno: '',
  pann_marca: '',
  pann_modello: '',
  pann_potenza_w: '',
  pann_quantita: 0,
  inv_marca: '',
  inv_modello: '',
  inv_potenza_kw: '',
  inv_quantita: 1,
  acc_presente: false,
  acc_marca: '',
  acc_capacita_kwh: 0,
  gen_dm_37_08: true,
  gen_comunicazione_comune: true,
  gen_modello_unico_p1: false,
  gen_modello_unico_p2: false,
  gen_schema_unifilare: false,
  gen_attestazione_titolo: false,
  gen_tica_areti: false,
  gen_transizione_50_ex_ante: false,
  gen_transizione_50_ex_post: false,
  gen_transizione_50_attestazione: false,
  ex_iban: '',
  ex_regime_ritiro: '',
  ex_qualita_richiedente: '',
  ex_tipologia_struttura: '',
  ex_denominazione_impianto: '',
  ex_codice_identificativo_connessione: '',
  ex_codice_rintracciabilita: '',
  ex_potenza_immissione_kw: 0,
  ex_configurazione_accumulo: '',
  ex_t50_ateco: '',
  ex_t50_tep_anno: 0,
  ex_t50_percentuale_riduzione: 0,
  ex_t50_fascia_agevolativa: '',
  ex_t50_investimento_totale_eur: 0,
};

// Pretty labels for select options. Stored value matches the code
// expected by the templates' `extras.*_label` fallbacks.
const QUALITA_RICHIEDENTE_OPTIONS = [
  { value: '', label: '— seleziona —' },
  { value: 'proprietario', label: 'Proprietario' },
  { value: 'usufruttuario', label: 'Usufruttuario' },
  { value: 'locatario', label: 'Locatario / affittuario' },
  { value: 'amministratore_condominio', label: 'Amministratore di condominio' },
  { value: 'altro', label: 'Altro titolo' },
];

const REGIME_RITIRO_OPTIONS = [
  { value: '', label: '— seleziona —' },
  { value: 'gse_po', label: 'Ritiro Dedicato GSE (RID)' },
  { value: 'gse_pmg', label: 'Prezzi minimi garantiti GSE' },
  { value: 'mercato', label: 'Vendita sul mercato libero' },
  { value: 'autoconsumo', label: 'Solo autoconsumo (no vendita)' },
];

const TIPOLOGIA_STRUTTURA_OPTIONS = [
  { value: '', label: '— seleziona —' },
  { value: 'edificio', label: 'Edificio esistente (tetto / copertura)' },
  { value: 'fuori_terra', label: 'Impianto a terra' },
  { value: 'pensilina', label: 'Pensilina / parcheggio' },
];

const CONFIG_ACCUMULO_OPTIONS = [
  { value: '', label: '— seleziona —' },
  { value: 'lato_produzione_mono', label: 'Lato produzione (DC monofase)' },
  { value: 'lato_dc', label: 'Lato DC integrato in inverter ibrido' },
  { value: 'lato_ac', label: 'Lato AC (post-contatore)' },
];

const DISTRIBUTORI: Array<{ value: string; label: string }> = [
  { value: 'e_distribuzione', label: 'E-Distribuzione (default nazionale)' },
  { value: 'areti', label: 'Areti (Roma)' },
  { value: 'unareti', label: 'Unareti (Milano)' },
  { value: 'altro', label: 'Altro' },
];

// ---------------------------------------------------------------------------

export default function NewPracticePage({
  params,
}: {
  // Next.js 15 ships `params` as a Promise on every page (server *and*
  // client). `React.use()` unwraps it ergonomically inside a client
  // component without needing to convert this whole page to async.
  params: Promise<{ id: string }>;
}) {
  const router = useRouter();
  const { id: leadId } = use(params);

  const [draft, setDraft] = useState<DraftPreview | null>(null);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(DEFAULT_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Initial draft fetch — populates the prefill bag and tells us whether
  // the lead is eligible (feedback='contract_signed') and whether a
  // practice already exists (so we can offer "Apri pratica" instead).
  useEffect(() => {
    let cancelled = false;
    api
      .get<DraftPreview>(`/v1/leads/${leadId}/practice/draft`)
      .then((res) => {
        if (cancelled) return;
        setDraft(res);

        // Hydrate form from prefill. Pannelli/inverter from the most
        // recent issued quote when available; ubicazione/distributore
        // from the roof. Numbers fall back to 0 — the form's required
        // markers force the installer to fix them before submit.
        const p = res.prefill;
        const panel = p.componenti?.pannelli ?? {};
        const inv = p.componenti?.inverter ?? {};
        const acc = p.componenti?.accumulo ?? {};

        setForm((f) => ({
          ...f,
          impianto_potenza_kw: p.impianto_potenza_kw ?? f.impianto_potenza_kw,
          impianto_pannelli_count:
            p.impianto_pannelli_count ?? f.impianto_pannelli_count,
          impianto_distributore:
            p.impianto_distributore ?? f.impianto_distributore,
          pann_marca: panel.marca ?? '',
          pann_modello: panel.modello ?? '',
          pann_potenza_w: panel.potenza_w ?? '',
          pann_quantita:
            p.impianto_pannelli_count ?? f.impianto_pannelli_count,
          inv_marca: inv.marca ?? '',
          inv_modello: inv.modello ?? '',
          acc_presente: !!acc.presente,
        }));
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const msg =
          err instanceof ApiError
            ? err.message
            : 'Impossibile caricare i dati del lead.';
        setDraftError(msg);
      });
    return () => {
      cancelled = true;
    };
  }, [leadId]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!draft) return;
    setSubmitting(true);
    setSubmitError(null);

    // Build template list from checkboxes. If the tenant is missing
    // required legal fields, we still allow submit — the worker will
    // persist the validation error onto the document row, and the
    // detail page surfaces it inline. The user can fix Settings and
    // hit "Rigenera" without re-creating the practice.
    const template_codes: string[] = [];
    if (form.gen_dm_37_08) template_codes.push('dm_37_08');
    if (form.gen_comunicazione_comune)
      template_codes.push('comunicazione_comune');
    if (form.gen_modello_unico_p1) template_codes.push('modello_unico_p1');
    if (form.gen_modello_unico_p2) template_codes.push('modello_unico_p2');
    if (form.gen_schema_unifilare) template_codes.push('schema_unifilare');
    if (form.gen_attestazione_titolo)
      template_codes.push('attestazione_titolo');
    if (form.gen_tica_areti) template_codes.push('tica_areti');
    if (form.gen_transizione_50_ex_ante)
      template_codes.push('transizione_50_ex_ante');
    if (form.gen_transizione_50_ex_post)
      template_codes.push('transizione_50_ex_post');
    if (form.gen_transizione_50_attestazione)
      template_codes.push('transizione_50_attestazione');
    if (template_codes.length === 0) {
      setSubmitError('Seleziona almeno un documento da generare.');
      setSubmitting(false);
      return;
    }

    // Componenti packaged so the renderer's loops can iterate without
    // null-checks. We always emit pannelli + inverter; accumulo only
    // when the toggle is on. Empty strings → null normalization keeps
    // the JSON stored small.
    const componenti_data: Record<string, unknown> = {
      pannelli: {
        marca: form.pann_marca || null,
        modello: form.pann_modello || null,
        potenza_w: form.pann_potenza_w || null,
        quantita: form.pann_quantita || null,
      },
      inverter: {
        marca: form.inv_marca || null,
        modello: form.inv_modello || null,
        potenza_kw: form.inv_potenza_kw || null,
        quantita: form.inv_quantita || null,
      },
    };
    if (form.acc_presente) {
      componenti_data.accumulo = {
        presente: true,
        marca: form.acc_marca || null,
        capacita_kwh: form.acc_capacita_kwh || null,
      };
    }

    // Build extras JSONB. Empty / zero values are dropped so the stored
    // blob stays compact — the mapper falls back to '—' in templates
    // when keys are absent.
    const extras: Record<string, unknown> = {};
    if (form.ex_iban) extras.iban = form.ex_iban;
    if (form.ex_regime_ritiro) {
      extras.regime_ritiro = form.ex_regime_ritiro;
      const rl = REGIME_RITIRO_OPTIONS.find(
        (o) => o.value === form.ex_regime_ritiro,
      );
      if (rl) extras.regime_ritiro_label = rl.label;
    }
    if (form.ex_qualita_richiedente) {
      extras.qualita_richiedente = form.ex_qualita_richiedente;
      const ql = QUALITA_RICHIEDENTE_OPTIONS.find(
        (o) => o.value === form.ex_qualita_richiedente,
      );
      if (ql) extras.qualita_richiedente_label = ql.label;
    }
    if (form.ex_tipologia_struttura) {
      extras.tipologia_struttura = form.ex_tipologia_struttura;
      const tl = TIPOLOGIA_STRUTTURA_OPTIONS.find(
        (o) => o.value === form.ex_tipologia_struttura,
      );
      if (tl) extras.tipologia_struttura_label = tl.label;
    }
    if (form.ex_denominazione_impianto)
      extras.denominazione_impianto = form.ex_denominazione_impianto;
    if (form.ex_codice_identificativo_connessione)
      extras.codice_identificativo_connessione =
        form.ex_codice_identificativo_connessione;
    if (form.ex_codice_rintracciabilita)
      extras.codice_rintracciabilita = form.ex_codice_rintracciabilita;
    if (form.ex_potenza_immissione_kw)
      extras.potenza_immissione_kw = form.ex_potenza_immissione_kw;
    if (form.ex_configurazione_accumulo) {
      extras.configurazione_accumulo = form.ex_configurazione_accumulo;
      const cl = CONFIG_ACCUMULO_OPTIONS.find(
        (o) => o.value === form.ex_configurazione_accumulo,
      );
      if (cl) extras.configurazione_accumulo_label = cl.label;
    }
    // Transizione 5.0 sub-blob — only emit when at least one T5.0
    // template is selected, to avoid polluting extras for non-T5.0 leads.
    const wantsT50 =
      form.gen_transizione_50_ex_ante ||
      form.gen_transizione_50_ex_post ||
      form.gen_transizione_50_attestazione;
    if (wantsT50) {
      const t50: Record<string, unknown> = {};
      if (form.ex_t50_ateco) t50.ateco = form.ex_t50_ateco;
      if (form.ex_t50_tep_anno) t50.tep_anno = form.ex_t50_tep_anno;
      if (form.ex_t50_percentuale_riduzione)
        t50.percentuale_riduzione = form.ex_t50_percentuale_riduzione;
      if (form.ex_t50_fascia_agevolativa)
        t50.fascia_agevolativa = form.ex_t50_fascia_agevolativa;
      if (form.ex_t50_investimento_totale_eur)
        t50.investimento_totale_eur = form.ex_t50_investimento_totale_eur;
      if (Object.keys(t50).length > 0) extras.transizione50 = t50;
    }

    const payload = {
      quote_id: draft.quote_id,
      impianto_potenza_kw: form.impianto_potenza_kw,
      impianto_pannelli_count: form.impianto_pannelli_count || null,
      impianto_pod: form.impianto_pod || null,
      impianto_distributore: form.impianto_distributore,
      impianto_data_inizio_lavori: form.impianto_data_inizio_lavori || null,
      impianto_data_fine_lavori: form.impianto_data_fine_lavori || null,
      catastale_foglio: form.catastale_foglio || null,
      catastale_particella: form.catastale_particella || null,
      catastale_subalterno: form.catastale_subalterno || null,
      componenti_data,
      extras,
      template_codes,
    };

    try {
      const res = await api.post<CreatePracticeResponse>(
        `/v1/leads/${leadId}/practice`,
        payload,
      );
      router.push(`/practices/${res.practice.id}`);
    } catch (err) {
      // 409 — pratica già esistente. Redirect alla detail della
      // pratica esistente (lo conosciamo dal draft).
      if (
        err instanceof ApiError &&
        err.status === 409 &&
        draft.existing_practice_id
      ) {
        router.push(`/practices/${draft.existing_practice_id}`);
        return;
      }
      const msg =
        err instanceof ApiError ? err.message : 'Creazione pratica fallita.';
      setSubmitError(msg);
      setSubmitting(false);
    }
  }

  // ----- Render --------------------------------------------------------------

  if (draftError) {
    return (
      <div className="space-y-3">
        <BackLink leadId={leadId} />
        <div className="rounded-xl bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {draftError}
        </div>
      </div>
    );
  }

  if (!draft) {
    return (
      <div className="flex items-center gap-2 py-12 text-sm text-on-surface-variant">
        <Loader2 size={16} className="animate-spin" /> Caricamento dati lead…
      </div>
    );
  }

  // Already-exists short-circuit. We don't auto-redirect — the user
  // intent here was "create new", so we tell them clearly and let
  // them pick (open existing, or back to lead).
  if (draft.has_existing && draft.existing_practice_id) {
    return (
      <div className="space-y-4">
        <BackLink leadId={leadId} />
        <div className="rounded-xl border border-blue-200 bg-blue-50 p-5 text-sm text-blue-900">
          <p className="font-semibold">
            Esiste già una pratica per questo lead:{' '}
            {draft.existing_practice_number}
          </p>
          <p className="mt-1 text-blue-800">
            In Sprint 1 è ammessa una sola pratica per lead. Apri quella
            esistente per generare nuovamente i documenti.
          </p>
          <div className="mt-3 flex gap-2">
            <Link
              href={`/practices/${draft.existing_practice_id}`}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-white hover:bg-primary/90"
            >
              Apri pratica
            </Link>
            <Link
              href={`/leads/${leadId}`}
              className="inline-flex items-center gap-1.5 rounded-lg border border-on-surface/10 px-3 py-1.5 text-xs font-medium text-on-surface-variant"
            >
              Torna al lead
            </Link>
          </div>
        </div>
      </div>
    );
  }

  if (!draft.eligible) {
    return (
      <div className="space-y-4">
        <BackLink leadId={leadId} />
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-5 text-sm text-amber-900">
          <p className="font-semibold">Lead non ancora eleggibile.</p>
          <p className="mt-1">
            La pratica GSE si crea quando il feedback del lead è impostato a{' '}
            <strong>contratto firmato</strong>. Aggiorna lo stato del lead e
            ritorna qui.
          </p>
        </div>
      </div>
    );
  }

  const dm3708Disabled =
    !form.gen_dm_37_08 || draft.missing_tenant_fields.length === 0;

  return (
    <div className="space-y-4">
      <BackLink leadId={leadId} />
      <header className="flex flex-wrap items-baseline justify-between gap-3">
        <h1 className="font-headline text-3xl font-bold tracking-tighter">
          Crea pratica GSE
        </h1>
        <span className="text-xs text-on-surface-variant">
          Numero proposto:{' '}
          <span className="font-headline font-bold text-primary">
            {draft.suggested_practice_number}
          </span>
        </span>
      </header>

      {draft.missing_tenant_fields.length > 0 && form.gen_dm_37_08 && (
        <div className="flex items-start gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <AlertTriangle size={18} className="mt-0.5 shrink-0" />
          <div className="space-y-1">
            <p className="font-semibold">
              Per generare il DM 37/08 servono dati legali del tenant.
            </p>
            <p>
              Mancano: <strong>{draft.missing_tenant_fields.join(', ')}</strong>.
              Compila i campi e poi rigenera il documento.
            </p>
            <Link
              href="/settings/legal"
              className="inline-block text-amber-900 underline hover:text-amber-950"
            >
              Vai a Impostazioni → Dati legali
            </Link>
          </div>
        </div>
      )}

      <form onSubmit={onSubmit} className="space-y-4">
        <Section title="Dati impianto">
          <Field label="Potenza (kWp)">
            <NumberInput
              step="0.01"
              value={form.impianto_potenza_kw}
              onChange={(v) => setForm({ ...form, impianto_potenza_kw: v })}
            />
          </Field>
          <Field label="Numero pannelli">
            <NumberInput
              value={form.impianto_pannelli_count}
              onChange={(v) =>
                setForm({ ...form, impianto_pannelli_count: v })
              }
            />
          </Field>
          <Field label="POD (codice fornitura)">
            <Input
              value={form.impianto_pod}
              onChange={(v) => setForm({ ...form, impianto_pod: v })}
            />
          </Field>
          <Field label="Distributore">
            <select
              value={form.impianto_distributore}
              onChange={(e) =>
                setForm({ ...form, impianto_distributore: e.target.value })
              }
              className="rounded-md border border-outline-variant bg-surface px-3 py-2 text-sm text-on-surface focus:border-primary focus:outline-none"
            >
              {DISTRIBUTORI.map((d) => (
                <option key={d.value} value={d.value}>
                  {d.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Data inizio lavori">
            <Input
              type="date"
              value={form.impianto_data_inizio_lavori}
              onChange={(v) =>
                setForm({ ...form, impianto_data_inizio_lavori: v })
              }
            />
          </Field>
          <Field label="Data fine lavori">
            <Input
              type="date"
              value={form.impianto_data_fine_lavori}
              onChange={(v) =>
                setForm({ ...form, impianto_data_fine_lavori: v })
              }
            />
          </Field>
        </Section>

        <Section title="Dati catastali (opzionale)">
          <Field label="Foglio">
            <Input
              value={form.catastale_foglio}
              onChange={(v) => setForm({ ...form, catastale_foglio: v })}
            />
          </Field>
          <Field label="Particella">
            <Input
              value={form.catastale_particella}
              onChange={(v) => setForm({ ...form, catastale_particella: v })}
            />
          </Field>
          <Field label="Subalterno">
            <Input
              value={form.catastale_subalterno}
              onChange={(v) => setForm({ ...form, catastale_subalterno: v })}
            />
          </Field>
        </Section>

        <Section title="Componenti — Pannelli">
          <Field label="Marca">
            <Input
              value={form.pann_marca}
              onChange={(v) => setForm({ ...form, pann_marca: v })}
            />
          </Field>
          <Field label="Modello">
            <Input
              value={form.pann_modello}
              onChange={(v) => setForm({ ...form, pann_modello: v })}
            />
          </Field>
          <Field label="Potenza singolo (es. 580 W)">
            <Input
              value={form.pann_potenza_w}
              onChange={(v) => setForm({ ...form, pann_potenza_w: v })}
            />
          </Field>
          <Field label="Quantità">
            <NumberInput
              value={form.pann_quantita}
              onChange={(v) => setForm({ ...form, pann_quantita: v })}
            />
          </Field>
        </Section>

        <Section title="Componenti — Inverter">
          <Field label="Marca">
            <Input
              value={form.inv_marca}
              onChange={(v) => setForm({ ...form, inv_marca: v })}
            />
          </Field>
          <Field label="Modello">
            <Input
              value={form.inv_modello}
              onChange={(v) => setForm({ ...form, inv_modello: v })}
            />
          </Field>
          <Field label="Potenza (es. 100 kW)">
            <Input
              value={form.inv_potenza_kw}
              onChange={(v) => setForm({ ...form, inv_potenza_kw: v })}
            />
          </Field>
          <Field label="Quantità">
            <NumberInput
              value={form.inv_quantita}
              onChange={(v) => setForm({ ...form, inv_quantita: v })}
            />
          </Field>
        </Section>

        <Section title="Componenti — Accumulo">
          <Field label="Accumulo presente?">
            <Toggle
              checked={form.acc_presente}
              onChange={(c) => setForm({ ...form, acc_presente: c })}
            />
          </Field>
          {form.acc_presente && (
            <>
              <Field label="Marca / modello accumulo">
                <Input
                  value={form.acc_marca}
                  onChange={(v) => setForm({ ...form, acc_marca: v })}
                />
              </Field>
              <Field label="Capacità (kWh)">
                <NumberInput
                  step="0.1"
                  value={form.acc_capacita_kwh}
                  onChange={(v) =>
                    setForm({ ...form, acc_capacita_kwh: v })
                  }
                />
              </Field>
            </>
          )}
        </Section>

        <Section title="Dati avanzati pratica (Sprint 2 — opzionali)">
          <Field label="IBAN per accredito GSE">
            <Input
              value={form.ex_iban}
              onChange={(v) => setForm({ ...form, ex_iban: v })}
            />
          </Field>
          <Field label="Regime ritiro / scambio">
            <Select
              value={form.ex_regime_ritiro}
              options={REGIME_RITIRO_OPTIONS}
              onChange={(v) => setForm({ ...form, ex_regime_ritiro: v })}
            />
          </Field>
          <Field label="Qualità richiedente">
            <Select
              value={form.ex_qualita_richiedente}
              options={QUALITA_RICHIEDENTE_OPTIONS}
              onChange={(v) =>
                setForm({ ...form, ex_qualita_richiedente: v })
              }
            />
          </Field>
          <Field label="Tipologia struttura">
            <Select
              value={form.ex_tipologia_struttura}
              options={TIPOLOGIA_STRUTTURA_OPTIONS}
              onChange={(v) => setForm({ ...form, ex_tipologia_struttura: v })}
            />
          </Field>
          <Field label="Denominazione impianto">
            <Input
              value={form.ex_denominazione_impianto}
              onChange={(v) =>
                setForm({ ...form, ex_denominazione_impianto: v })
              }
            />
          </Field>
          <Field label="Potenza in immissione (kW)">
            <NumberInput
              step="0.01"
              value={form.ex_potenza_immissione_kw}
              onChange={(v) =>
                setForm({ ...form, ex_potenza_immissione_kw: v })
              }
            />
          </Field>
          <Field label="Configurazione accumulo">
            <Select
              value={form.ex_configurazione_accumulo}
              options={CONFIG_ACCUMULO_OPTIONS}
              onChange={(v) =>
                setForm({ ...form, ex_configurazione_accumulo: v })
              }
            />
          </Field>
          <Field label="Codice identificativo connessione (MU Pt. II)">
            <Input
              value={form.ex_codice_identificativo_connessione}
              onChange={(v) =>
                setForm({ ...form, ex_codice_identificativo_connessione: v })
              }
            />
          </Field>
          <Field label="Codice rintracciabilità">
            <Input
              value={form.ex_codice_rintracciabilita}
              onChange={(v) =>
                setForm({ ...form, ex_codice_rintracciabilita: v })
              }
            />
          </Field>
        </Section>

        {(form.gen_transizione_50_ex_ante ||
          form.gen_transizione_50_ex_post ||
          form.gen_transizione_50_attestazione) && (
          <Section title="Transizione 5.0 — Dati specifici">
            <Field label="Codice ATECO attività">
              <Input
                value={form.ex_t50_ateco}
                onChange={(v) => setForm({ ...form, ex_t50_ateco: v })}
              />
            </Field>
            <Field label="Risparmio annuo stimato (tep)">
              <NumberInput
                step="0.001"
                value={form.ex_t50_tep_anno}
                onChange={(v) => setForm({ ...form, ex_t50_tep_anno: v })}
              />
            </Field>
            <Field label="Percentuale riduzione consumi (%)">
              <NumberInput
                step="0.01"
                value={form.ex_t50_percentuale_riduzione}
                onChange={(v) =>
                  setForm({ ...form, ex_t50_percentuale_riduzione: v })
                }
              />
            </Field>
            <Field label="Fascia agevolativa">
              <Input
                value={form.ex_t50_fascia_agevolativa}
                onChange={(v) =>
                  setForm({ ...form, ex_t50_fascia_agevolativa: v })
                }
              />
            </Field>
            <Field label="Investimento totale agevolabile (€)">
              <NumberInput
                step="1"
                value={form.ex_t50_investimento_totale_eur}
                onChange={(v) =>
                  setForm({ ...form, ex_t50_investimento_totale_eur: v })
                }
              />
            </Field>
          </Section>
        )}

        <Section title="Documenti da generare">
          <CheckboxRow
            checked={form.gen_dm_37_08}
            onChange={(c) => setForm({ ...form, gen_dm_37_08: c })}
            title="DM 37/08 — Dichiarazione di conformità"
            subtitle="Richiede i dati del responsabile tecnico del tenant."
          />
          <CheckboxRow
            checked={form.gen_comunicazione_comune}
            onChange={(c) =>
              setForm({ ...form, gen_comunicazione_comune: c })
            }
            title="Comunicazione al Comune"
            subtitle="Comunicazione di fine lavori (DPR 380/2001 art. 6)."
          />
          <CheckboxRow
            checked={form.gen_modello_unico_p1}
            onChange={(c) => setForm({ ...form, gen_modello_unico_p1: c })}
            title="Modello Unico — Parte I (pre-lavori)"
            subtitle="Per impianti FV ≤200 kW (D.Lgs. 199/2021). Richiede qualità richiedente, regime ritiro, tipologia struttura."
          />
          <CheckboxRow
            checked={form.gen_modello_unico_p2}
            onChange={(c) => setForm({ ...form, gen_modello_unico_p2: c })}
            title="Modello Unico — Parte II (as-built)"
            subtitle="Da inviare a fine lavori. Richiede il codice identificativo connessione."
          />
          <CheckboxRow
            checked={form.gen_schema_unifilare}
            onChange={(c) => setForm({ ...form, gen_schema_unifilare: c })}
            title="Schema elettrico unifilare (CEI 0-21)"
            subtitle="Allegato obbligatorio al MU e alla TICA."
          />
          <CheckboxRow
            checked={form.gen_attestazione_titolo}
            onChange={(c) =>
              setForm({ ...form, gen_attestazione_titolo: c })
            }
            title="Modulo ATR — Attestazione titolo richiedente"
            subtitle="Richiede qualità richiedente. Allegato MU per IRETI/Unareti."
          />
          <CheckboxRow
            checked={form.gen_tica_areti}
            onChange={(c) => setForm({ ...form, gen_tica_areti: c })}
            title="Istanza TICA — Allegato 1 ARERA 109/2021"
            subtitle="Per impianti >200 kW o quando non si usa il MU semplificato."
          />
          <CheckboxRow
            checked={form.gen_transizione_50_ex_ante}
            onChange={(c) =>
              setForm({ ...form, gen_transizione_50_ex_ante: c })
            }
            title="Transizione 5.0 — Allegato VIII (ex-ante)"
            subtitle="Certificazione pre-investimento. Richiede dati T5.0."
          />
          <CheckboxRow
            checked={form.gen_transizione_50_ex_post}
            onChange={(c) =>
              setForm({ ...form, gen_transizione_50_ex_post: c })
            }
            title="Transizione 5.0 — Allegato X (ex-post)"
            subtitle="Certificazione post-realizzazione."
          />
          <CheckboxRow
            checked={form.gen_transizione_50_attestazione}
            onChange={(c) =>
              setForm({ ...form, gen_transizione_50_attestazione: c })
            }
            title="Transizione 5.0 — Allegato V (attestazione)"
            subtitle="Possesso perizia + certificazione contabile."
          />
          {/* dm3708Disabled is a hint for future per-template inline disable. */}
          <input type="hidden" data-dm-disabled={String(dm3708Disabled)} />
        </Section>

        <div className="flex items-center justify-end gap-3 pt-2">
          {submitError && (
            <span className="text-xs text-rose-700">{submitError}</span>
          )}
          <button
            type="submit"
            disabled={submitting}
            className="inline-flex items-center gap-2 rounded-full bg-gradient-primary px-6 py-3 text-sm font-bold text-on-primary shadow-ambient-sm transition-all hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {submitting ? (
              <>
                <Loader2 className="animate-spin" size={14} /> Creazione in
                corso…
              </>
            ) : (
              <>
                <FileText size={14} /> Crea pratica e genera documenti
              </>
            )}
          </button>
        </div>
      </form>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Tiny presentational primitives (mirror quote/page.tsx)
// ---------------------------------------------------------------------------

function BackLink({ leadId }: { leadId: string }) {
  return (
    <Link
      href={`/leads/${leadId}`}
      className="inline-flex items-center gap-1 text-xs font-medium text-on-surface-variant transition-colors hover:text-primary"
    >
      <ArrowLeft size={12} strokeWidth={2.25} /> Torna al lead
    </Link>
  );
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
    <label
      className={`flex flex-col gap-1 text-xs ${wide ? 'sm:col-span-2' : ''}`}
    >
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
  step = '1',
}: {
  value: number;
  onChange: (v: number) => void;
  step?: string;
}) {
  return (
    <input
      type="number"
      step={step}
      value={value}
      onChange={(e) => {
        const n = Number(e.target.value);
        onChange(Number.isFinite(n) ? n : 0);
      }}
      className="rounded-md border border-outline-variant bg-surface px-3 py-2 text-sm text-on-surface focus:border-primary focus:outline-none"
    />
  );
}

function Select({
  value,
  options,
  onChange,
}: {
  value: string;
  options: Array<{ value: string; label: string }>;
  onChange: (v: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded-md border border-outline-variant bg-surface px-3 py-2 text-sm text-on-surface focus:border-primary focus:outline-none"
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
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

function CheckboxRow({
  checked,
  onChange,
  title,
  subtitle,
}: {
  checked: boolean;
  onChange: (c: boolean) => void;
  title: string;
  subtitle: string;
}) {
  return (
    <label className="flex items-start gap-3 rounded-lg border border-outline-variant bg-surface px-3 py-2.5 sm:col-span-2">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-0.5 h-4 w-4 cursor-pointer accent-primary"
      />
      <span className="flex flex-col gap-0.5">
        <span className="text-sm font-medium text-on-surface">{title}</span>
        <span className="text-xs text-on-surface-variant">{subtitle}</span>
      </span>
    </label>
  );
}
