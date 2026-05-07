/**
 * Italian labels for the wizard_group sector taxonomy used across the
 * funnel v3 surfaces. Single source of truth — both the contatti table
 * and the territorio scan-results-panel render sector chips.
 *
 * The keys mirror `ateco_google_types.wizard_group` in the database
 * (migrations 0097/0099) and the Python-side mapping in
 * `apps/api/src/services/places_to_sector.py`.
 */

export const SECTOR_LABELS: Record<string, string> = {
  industry_heavy: 'Manifatturiero pesante',
  industry_light: 'Manifatturiero leggero',
  food_production: 'Produzione alimentare',
  logistics: 'Logistica',
  retail_gdo: 'Grande distribuzione',
  hospitality_large: 'Ricettivo grande',
  hospitality_food_service: 'Ristorazione collettiva',
  healthcare: 'Sanitario',
  healthcare_private: 'Sanitario privato',
  agricultural_intensive: 'Agricolo intensivo',
  automotive: 'Automotive',
  education: 'Istruzione',
  personal_services: 'Servizi alla persona',
  professional_offices: 'Studi professionali',
  horeca: 'HoReCa',
  amministratori_condominio: 'Amministratori di condominio',
};

/**
 * Resolve a sector slug to its Italian label. Returns the slug itself
 * (unmodified) when no mapping exists — surface unmapped sectors loudly
 * so we notice and add them — or "—" when the slug is null/empty.
 */
export function sectorLabel(s: string | null | undefined): string {
  if (!s) return '—';
  return SECTOR_LABELS[s] ?? s;
}
