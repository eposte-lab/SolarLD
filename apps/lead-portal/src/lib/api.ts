/**
 * Tiny fetch wrapper shared across server components and client
 * handlers. Kept deliberately small so `vitest` unit-tests can mock
 * `fetch` globally without wrestling with axios/swr abstractions.
 */
export const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export type PublicLead = {
  public_slug: string;
  score: number;
  score_tier: 'hot' | 'warm' | 'cold' | 'rejected';
  pipeline_status:
    | 'new'
    | 'sent'
    | 'delivered'
    | 'opened'
    | 'clicked'
    | 'engaged'
    | 'whatsapp'
    | 'appointment'
    | 'closed_won'
    | 'closed_lost'
    | 'blacklisted';
  outreach_sent_at: string | null;
  rendering_image_url: string | null;
  rendering_video_url: string | null;
  rendering_gif_url: string | null;
  roi_data: {
    estimated_kwp?: number;
    yearly_savings_eur?: number;
    payback_years?: number;
    co2_tonnes_25_years?: number;
    investment_eur?: number;
  };
  subjects: {
    type: 'b2b' | 'b2c' | 'unknown';
    business_name?: string | null;
    owner_first_name?: string | null;
  } | null;
  roofs: {
    address?: string | null;
    cap?: string | null;
    comune?: string | null;
    provincia?: string | null;
    area_sqm?: number | null;
    estimated_kwp?: number | null;
    estimated_yearly_kwh?: number | null;
    // Cached compute_full_derivations snapshot (cost / sizing /
    // monthly curve / coverage). Same dict the dashboard inspector
    // and preventivo PDF read from. Single source of truth — when
    // present, the lead-portal ROI block prefers this over
    // lead.roi_data so a bolletta upload that refreshed the
    // derivations doesn't leave the portal showing stale numbers.
    derivations?: Record<string, unknown> | null;
  } | null;
  tenant: {
    business_name: string;
    brand_logo_url: string | null;
    brand_primary_color: string;
    whatsapp_number: string | null;
    contact_email: string | null;
    /* GDPR footer fields (may be null for tenants pre-Sprint 6.5). */
    legal_name: string | null;
    vat_number: string | null;
    legal_address: string | null;
    /* Sprint 8 Fase A.2 — "Chi siamo" narrative. */
    about_md: string | null;
    about_year_founded: number | null;
    about_team_size: string | null;
    about_certifications: string[] | null;
    about_hero_image_url: string | null;
    about_tagline: string | null;
  } | null;
};

export type LeadFetchResult =
  | { kind: 'ok'; lead: PublicLead }
  | { kind: 'not_found' }
  | { kind: 'gone' }; // 410 when the lead has opted out

export async function fetchPublicLead(slug: string): Promise<LeadFetchResult> {
  const res = await fetch(
    `${API_URL}/v1/public/lead/${encodeURIComponent(slug)}`,
    { next: { revalidate: 3600 } },
  );
  if (res.status === 404) return { kind: 'not_found' };
  if (res.status === 410) return { kind: 'gone' };
  if (!res.ok) throw new Error(`Failed to load lead: ${res.status}`);
  const lead = (await res.json()) as PublicLead;
  return { kind: 'ok', lead };
}

/** Italian-locale number formatter — consistent with the email template. */
export function formatEuro(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `€ ${Math.round(value).toLocaleString('it-IT')}`;
}

export function formatYears(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${value.toFixed(1)} anni`;
}

/** Build a WhatsApp wa.me deep link from an Italian-style number. */
export function whatsappUrl(
  number: string | null | undefined,
  preset: string,
): string | null {
  if (!number) return null;
  const digits = number.replace(/\D/g, '');
  if (!digits) return null;
  return `https://wa.me/${digits}?text=${encodeURIComponent(preset)}`;
}

/**
 * Friendly greeting + roof address mashup for the portal hero.
 * Picks B2B vs B2C tone to match the outreach templates.
 */
export function leadHeroCopy(lead: PublicLead): { title: string; subtitle: string } {
  const subject = lead.subjects;
  const roof = lead.roofs;
  const address = [roof?.address, roof?.comune].filter(Boolean).join(', ');
  if (subject?.type === 'b2b' && subject.business_name) {
    return {
      title: `Ecco come si presenterebbe la sede di ${subject.business_name}`,
      subtitle: address ? `${address} (${roof?.cap ?? ''})` : 'La vostra sede con il fotovoltaico',
    };
  }
  if (subject?.type === 'b2c') {
    const first = subject.owner_first_name?.trim();
    return {
      title: first
        ? `${first}, ecco come potrebbe essere la vostra casa`
        : 'Ecco come potrebbe essere la vostra casa',
      subtitle: address ? `${address} (${roof?.cap ?? ''})` : 'La vostra casa con il fotovoltaico',
    };
  }
  return {
    title: 'Ecco come potrebbe essere il tuo tetto con il fotovoltaico',
    subtitle: address ? `${address} (${roof?.cap ?? ''})` : 'Simulazione personalizzata',
  };
}
