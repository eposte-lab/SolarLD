/**
 * Contatti display helpers + row shape — NO server-only marker so
 * client components (contatti-table.tsx) can import the type + the
 * pure resolution helpers without dragging the Supabase server client
 * into the bundle.
 *
 * The server-side query layer (`lib/data/contatti.ts`) re-exports
 * these so existing imports keep working.
 *
 * Display priority for v3 candidates: enrichment.places.* + scraped_data
 * + contact_extraction. Legacy v2 (Atoka) candidates fall back to flat
 * columns (business_name, hq_city, etc.). All resolvers tolerate NULL.
 */

export type SolarVerdict =
  | 'accepted'
  | 'rejected_tech'
  | 'no_building'
  | 'api_error'
  | 'skipped_below_gate';

export interface ProxyScoreData {
  overall_score?: number | null;
  building_quality_score?: number | null;
  contact_completeness_score?: number | null;
  predicted_size_category?: string | null;
  recommended_for_rendering?: boolean | null;
}

export interface ContactExtraction {
  best_email?: string | null;
  best_phone?: string | null;
  decision_maker_phone?: string | null;
}

export interface PlacesEnrichment {
  display_name?: string | null;
  formatted_address?: string | null;
  website?: string | null;
  phone?: string | null;
  types?: string[];
}

export interface ContattoRow {
  id: string;
  scan_id: string;
  territory_id: string | null;
  vat_number: string | null;
  // legacy v2 (Atoka) fallback fields — NULL on v3 rows
  business_name: string | null;
  ateco_code: string | null;
  employees: number | null;
  revenue_eur: number | null;
  hq_city: string | null;
  hq_province: string | null;
  // funnel state
  score: number | null; // legacy L3 Haiku score (also lives in proxy_score_data.overall_score for v3)
  stage: number; // 1-5
  solar_verdict: SolarVerdict | null;
  roof_id: string | null;
  /** Resolved by `listContatti` via roof_id → leads join. NULL when
   *  the candidate hasn't been promoted to a lead yet. */
  lead_id?: string | null;
  created_at: string;
  // v3 enrichment (NULL on legacy v2 rows)
  predicted_sector: string | null;
  building_quality_score: number | null;
  proxy_score_data: ProxyScoreData | null;
  scraped_data: Record<string, unknown> | null;
  contact_extraction: ContactExtraction | null;
  enrichment: { places?: PlacesEnrichment | null } | null;
  territories: { name: string; type: string; code: string } | null;
}

// ---------------------------------------------------------------------------
// Display-value resolvers
// ---------------------------------------------------------------------------

export function displayName(c: ContattoRow): string {
  const scrapedLegal =
    typeof c.scraped_data?.legal_name === 'string'
      ? (c.scraped_data.legal_name as string)
      : null;
  return (
    scrapedLegal ??
    c.enrichment?.places?.display_name ??
    c.business_name ??
    c.vat_number ??
    '—'
  );
}

export function displayCity(c: ContattoRow): string | null {
  // v3: parse the formatted_address Google Places returns. Italian
  // addresses look like "Via Foo 12, 20100 Milano MI, Italy" — we
  // want "Milano" out of the second-to-last comma segment.
  const fa = c.enrichment?.places?.formatted_address ?? null;
  if (fa) {
    const parts = fa.split(',').map((s) => s.trim());
    const cityCap = parts.length >= 2 ? parts[parts.length - 2] : null;
    if (cityCap) {
      const m = cityCap.match(/^\d{5}\s+(.+?)(?:\s+[A-Z]{2})?$/);
      if (m && m[1]) return m[1];
    }
  }
  return c.hq_city ?? null;
}

export function displayProvince(c: ContattoRow): string | null {
  const fa = c.enrichment?.places?.formatted_address ?? null;
  if (fa) {
    const parts = fa.split(',').map((s) => s.trim());
    const cityCap = parts.length >= 2 ? parts[parts.length - 2] : null;
    if (cityCap) {
      const m = cityCap.match(/\s([A-Z]{2})$/);
      if (m && m[1]) return m[1];
    }
  }
  return c.hq_province ?? null;
}

export function displayEmail(c: ContattoRow): string | null {
  return c.contact_extraction?.best_email ?? null;
}

export function displayPhone(c: ContattoRow): string | null {
  return (
    c.contact_extraction?.decision_maker_phone ??
    c.contact_extraction?.best_phone ??
    c.enrichment?.places?.phone ??
    null
  );
}

export function displayOverallScore(c: ContattoRow): number | null {
  return c.proxy_score_data?.overall_score ?? c.score ?? null;
}

export function displayWebsite(c: ContattoRow): string | null {
  const scrapedUrl =
    typeof c.scraped_data?.website_url === 'string'
      ? (c.scraped_data.website_url as string)
      : null;
  return scrapedUrl ?? c.enrichment?.places?.website ?? null;
}
