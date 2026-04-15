import type {
  InstallerFeedback,
  LeadScoreTier,
  LeadStatus,
  OutreachChannel,
  SubjectType,
} from './enums';

export interface Roof {
  id: string;
  tenant_id: string;
  territory_id: string | null;
  lat: number;
  lng: number;
  address: string | null;
  cap: string | null;
  comune: string | null;
  provincia: string | null;
  area_sqm: number | null;
  estimated_kwp: number | null;
  estimated_yearly_kwh: number | null;
  exposure: string | null;
  pitch_degrees: number | null;
  shading_score: number | null;
  has_existing_pv: boolean;
  classification: SubjectType;
}

export interface Subject {
  id: string;
  tenant_id: string;
  roof_id: string;
  type: SubjectType;
  business_name: string | null;
  vat_number: string | null;
  ateco_code: string | null;
  ateco_description: string | null;
  yearly_revenue_cents: number | null;
  employees: number | null;
  decision_maker_name: string | null;
  decision_maker_role: string | null;
  decision_maker_email: string | null;
  owner_first_name: string | null;
  owner_last_name: string | null;
  postal_address_line1: string | null;
  postal_cap: string | null;
  postal_city: string | null;
  postal_province: string | null;
}

export interface Lead {
  id: string;
  tenant_id: string;
  roof_id: string;
  subject_id: string;
  public_slug: string;
  score: number;
  score_tier: LeadScoreTier;
  score_breakdown: Record<string, number>;
  rendering_image_url: string | null;
  rendering_video_url: string | null;
  rendering_gif_url: string | null;
  roi_data: {
    investment_eur?: number;
    yearly_savings_eur?: number;
    payback_years?: number;
  };
  outreach_channel: OutreachChannel | null;
  outreach_sent_at: string | null;
  outreach_delivered_at: string | null;
  outreach_opened_at: string | null;
  outreach_clicked_at: string | null;
  dashboard_visited_at: string | null;
  whatsapp_initiated_at: string | null;
  pipeline_status: LeadStatus;
  feedback: InstallerFeedback | null;
  feedback_notes: string | null;
  feedback_at: string | null;
  contract_value_cents: number | null;
  created_at: string;
  updated_at: string;
  subjects?: Subject;
  roofs?: Roof;
}
