import type { TenantStatus, TenantTier } from './enums';

export interface Tenant {
  id: string;
  business_name: string;
  vat_number: string | null;
  contact_email: string;
  contact_phone: string | null;
  whatsapp_number: string | null;
  brand_logo_url: string | null;
  brand_primary_color: string;
  email_from_domain: string | null;
  email_from_name: string | null;
  tier: TenantTier;
  monthly_rate_cents: number;
  contract_start_date: string | null;
  contract_end_date: string | null;
  status: TenantStatus;
  stripe_customer_id: string | null;
  stripe_subscription_id: string | null;
  settings: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}
