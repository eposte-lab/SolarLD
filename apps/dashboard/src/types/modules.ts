/**
 * TypeScript mirror of the Pydantic module schemas in
 * `apps/api/src/services/tenant_module_service.py`.
 *
 * These types are hand-written (not generated) so additions require a
 * coordinated edit on both sides — trading some boilerplate for an
 * explicit compile-time check that the frontend's understanding
 * matches the backend's contract.
 *
 * The five module keys and their config shapes are the stable interface
 * between dashboard forms and the `/v1/modules/*` API.
 */
export type ModuleKey =
  | 'sorgente'
  | 'tecnico'
  | 'economico'
  | 'outreach'
  | 'crm';

export const MODULE_KEYS: readonly ModuleKey[] = [
  'sorgente',
  'tecnico',
  'economico',
  'outreach',
  'crm',
] as const;

export interface SorgenteConfig {
  ateco_codes: string[];
  min_employees: number | null;
  max_employees: number | null;
  min_revenue_eur: number | null;
  max_revenue_eur: number | null;
  province: string[];
  regioni: string[];
  cap: string[];
  reddito_min_eur: number;
  case_unifamiliari_pct_min: number;
}

export type Orientamento = 'N' | 'NE' | 'E' | 'SE' | 'S' | 'SO' | 'O' | 'NO';

export interface TecnicoConfig {
  min_kwp: number;
  min_area_sqm: number;
  max_shading: number;
  min_exposure_score: number;
  orientamenti_ok: Orientamento[];
  solar_gate_pct: number;
  solar_gate_min_candidates: number;
}

export interface EconomicoConfig {
  ticket_medio_eur: number;
  roi_target_years: number;
  budget_scan_eur: number;
  budget_outreach_eur_month: number;
}

export interface OutreachChannels {
  email: boolean;
  postal: boolean;
  whatsapp: boolean;
  meta_ads: boolean;
}

export interface OutreachConfig {
  channels: OutreachChannels;
  tone_of_voice: string;
  cta_primary: string;
}

export interface CRMConfig {
  webhook_url: string | null;
  webhook_secret: string | null;
  pipeline_labels: string[];
  sla_hours_first_touch: number;
}

/** Discriminated-union convenience for code that walks all modules. */
export type ModuleConfigByKey = {
  sorgente: SorgenteConfig;
  tecnico: TecnicoConfig;
  economico: EconomicoConfig;
  outreach: OutreachConfig;
  crm: CRMConfig;
};

export interface TenantModule<K extends ModuleKey = ModuleKey> {
  tenant_id: string;
  module_key: K;
  config: ModuleConfigByKey[K];
  active: boolean;
  version: number;
  updated_at?: string | null;
}

export interface ModuleListResponse {
  modules: TenantModule[];
  wizard_complete: boolean;
}

export interface ModulePreviewResponse<C = Record<string, unknown>> {
  valid: boolean;
  normalised: C;
  estimate: Record<string, unknown>;
}

/** Human-readable labels for UI rendering. */
export const MODULE_LABELS: Record<ModuleKey, string> = {
  sorgente: 'Sorgente',
  tecnico: 'Tecnico',
  economico: 'Economico',
  outreach: 'Outreach',
  crm: 'CRM',
};

export const MODULE_DESCRIPTIONS: Record<ModuleKey, string> = {
  sorgente:
    'Dove cercare prospect: settori ATECO, dimensione aziendale, geografia (o — per il B2C — fascia di reddito CAP).',
  tecnico:
    'Cosa rende un tetto qualificato: kW minimi, superficie, esposizione, e percentuale di candidati che passano dal filtro Solar.',
  economico:
    'Prezzi di riferimento e budget per scan/mese. Influenza il cap automatico della pipeline.',
  outreach:
    'Canali attivi (email, lettera, WhatsApp, Meta Ads), tone of voice, CTA preferita.',
  crm:
    'Integrazione downstream: webhook di uscita, HMAC, label pipeline, SLA primo contatto.',
};

// ---------------------------------------------------------------------------
// Default config per module — MUST match the Pydantic defaults in
// `apps/api/src/services/tenant_module_service.py` and the backfill
// in migration 0032. The frontend uses these to hydrate missing keys
// before rendering a form, so forms never see `undefined` arrays /
// nested objects when the backend returns `{}` (brand-new tenant).
// ---------------------------------------------------------------------------

export const DEFAULT_SORGENTE: SorgenteConfig = {
  ateco_codes: [],
  min_employees: 20,
  max_employees: 250,
  min_revenue_eur: 2_000_000,
  max_revenue_eur: 50_000_000,
  province: [],
  regioni: [],
  cap: [],
  reddito_min_eur: 35_000,
  case_unifamiliari_pct_min: 40,
};

export const DEFAULT_TECNICO: TecnicoConfig = {
  min_kwp: 50,
  min_area_sqm: 500,
  max_shading: 0.4,
  min_exposure_score: 0.7,
  orientamenti_ok: ['S', 'SE', 'SO', 'E', 'O'],
  solar_gate_pct: 0.2,
  solar_gate_min_candidates: 20,
};

export const DEFAULT_ECONOMICO: EconomicoConfig = {
  ticket_medio_eur: 25_000,
  roi_target_years: 6,
  budget_scan_eur: 50,
  budget_outreach_eur_month: 2_000,
};

export const DEFAULT_OUTREACH: OutreachConfig = {
  channels: {
    email: true,
    postal: false,
    whatsapp: false,
    meta_ads: false,
  },
  tone_of_voice: 'professionale-diretto',
  cta_primary: 'Prenota un sopralluogo gratuito',
};

export const DEFAULT_CRM: CRMConfig = {
  webhook_url: null,
  webhook_secret: null,
  pipeline_labels: ['nuovo', 'contattato', 'in-valutazione', 'preventivo', 'chiuso'],
  sla_hours_first_touch: 24,
};

export const DEFAULT_MODULE_CONFIGS: ModuleConfigByKey = {
  sorgente: DEFAULT_SORGENTE,
  tecnico: DEFAULT_TECNICO,
  economico: DEFAULT_ECONOMICO,
  outreach: DEFAULT_OUTREACH,
  crm: DEFAULT_CRM,
};

/**
 * Merge a (possibly empty / partial) config with the default for that
 * module key. Deep merge is intentionally shallow-only: the nested
 * objects in our schema (e.g. `outreach.channels`) are also merged
 * with their defaults, but we don't go deeper than one level because
 * no module config has three-level-deep structure.
 */
export function withModuleDefaults<K extends ModuleKey>(
  key: K,
  partial: Partial<ModuleConfigByKey[K]> | null | undefined,
): ModuleConfigByKey[K] {
  const defaults = DEFAULT_MODULE_CONFIGS[key];
  if (!partial || typeof partial !== 'object') {
    return defaults;
  }
  const merged: Record<string, unknown> = {
    ...(defaults as unknown as Record<string, unknown>),
  };
  for (const [k, v] of Object.entries(partial)) {
    if (v === undefined || v === null) continue;
    const defaultValue = (defaults as unknown as Record<string, unknown>)[k];
    // One-level deep merge for nested objects like outreach.channels.
    if (
      typeof v === 'object' &&
      !Array.isArray(v) &&
      typeof defaultValue === 'object' &&
      defaultValue !== null &&
      !Array.isArray(defaultValue)
    ) {
      merged[k] = { ...(defaultValue as object), ...(v as object) };
    } else {
      merged[k] = v;
    }
  }
  return merged as unknown as ModuleConfigByKey[K];
}
