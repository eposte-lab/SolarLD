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
  /** Anti-spam audit flags from lead_quality_validator (post-L5). */
  flags?: string[] | null;
}

export interface ContactExtraction {
  best_email?: string | null;
  /** Confidence level set by `web_scraper.extract_best_email`:
   *   "alta"  → named role (direzione@, amministrazione@)
   *   "media" → first generic email scraped from contact pages
   *   "bassa" → privacy/DPO fallback OR pattern-inferred (info@<dom>)
   */
  best_email_confidence?: 'alta' | 'media' | 'bassa' | null;
  /** Origin tag: 'named_role' | 'generic' | 'privacy_dpo' | 'inferred_pattern' */
  best_email_type?: string | null;
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

/**
 * Parse an Italian Google Places `formatted_address` into { city, province }.
 *
 * Google returns the city segment in different positions depending on how
 * complete the place is, and — crucially — often WITHOUT a trailing ", Italy":
 *   "Via Foo 12, 20100 Milano MI, Italy"                → city is parts[-2]
 *   "Via Marandoli, 2, 82030 San Salvatore Telesino BN" → city is parts[-1]
 *   "Viale dell'Artigianato, 71036 Lucera FG"           → city is parts[-1]
 *   "81050 Pastorano CE"                                → single segment
 * The city always lives in the segment shaped "<CAP> <City…> [PROV]". We scan
 * segments from the end (most specific first) for that pattern, so the parse is
 * position-independent and tolerates the ", Italy" suffix being present OR not.
 *
 * The previous code hard-coded `parts[-2]`; on the suffix-less shapes the v3
 * discovery actually produces it grabbed the street number ("2") instead of
 * the city — leaving the Comune column blank. hq_city/hq_province are NULL on
 * v3 candidates (legacy v2/Atoka columns), so there was no fallback either.
 */
function parseItalianPlaces(fa: string | null): {
  city: string | null;
  province: string | null;
} {
  if (!fa) return { city: null, province: null };
  const parts = fa
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  for (let i = parts.length - 1; i >= 0; i--) {
    const m = parts[i]?.match(/^\d{5}\s+(.+?)(?:\s+([A-Z]{2}))?$/);
    if (m && m[1]) return { city: m[1].trim(), province: m[2] ?? null };
  }
  return { city: null, province: null };
}

export function displayCity(c: ContattoRow): string | null {
  return (
    parseItalianPlaces(c.enrichment?.places?.formatted_address ?? null).city ??
    c.hq_city ??
    null
  );
}

export function displayProvince(c: ContattoRow): string | null {
  return (
    parseItalianPlaces(c.enrichment?.places?.formatted_address ?? null)
      .province ??
    c.hq_province ??
    null
  );
}

// Defense-in-depth filter: reject obvious placeholders/example addresses
// that may have leaked through the scraper (e.g. a stale `info@example.com`
// in a website template). Mirrors the server-side `_looks_like_example`
// in apps/api/src/services/email_extractor.py.
function looksLikeExampleEmail(email: string): boolean {
  const lower = email.toLowerCase();
  return (
    lower.includes('example') ||
    lower.includes('esempio') ||
    lower.includes('dummy') ||
    lower.includes('placeholder') ||
    lower.startsWith('your@') ||
    lower.startsWith('user@') ||
    lower.startsWith('nome@') ||
    lower.startsWith('cognome@')
  );
}

// Reject phone strings that are obviously not phones — repeated digits
// (33333333333), VAT-numbers/fiscal codes the scraper occasionally swept
// up by mistake (anything starting with 3+ leading zeros), or numbers
// that are too short to be Italian phones (< 7 digits after stripping
// non-digits). Italian landlines are 9-11 digits, mobiles 9-10.
function looksLikeRealPhone(phone: string): boolean {
  const digits = phone.replace(/\D/g, '');
  if (digits.length < 7) return false;
  if (/^0{3,}/.test(digits)) return false;
  if (/^(\d)\1+$/.test(digits)) return false;
  return true;
}

export function displayEmail(c: ContattoRow): string | null {
  const email = c.contact_extraction?.best_email ?? null;
  if (!email) return null;
  if (looksLikeExampleEmail(email)) return null;
  return email;
}

export function displayPhone(c: ContattoRow): string | null {
  // Prefer Google Places phone over the scraped best_phone — the Places
  // value is verified by Google, the scraper sometimes captures VAT
  // numbers or all-3s placeholders when the website's contact section
  // is malformed.
  const candidates = [
    c.contact_extraction?.decision_maker_phone ?? null,
    c.enrichment?.places?.phone ?? null,
    c.contact_extraction?.best_phone ?? null,
  ];
  for (const p of candidates) {
    if (p && looksLikeRealPhone(p)) return p;
  }
  return null;
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
