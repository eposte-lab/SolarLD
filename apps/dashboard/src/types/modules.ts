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
