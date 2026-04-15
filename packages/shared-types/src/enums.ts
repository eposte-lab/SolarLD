export type TenantTier = 'founding' | 'pro' | 'enterprise';
export type TenantStatus = 'onboarding' | 'active' | 'paused' | 'churned';

export type TerritoryType = 'cap' | 'comune' | 'provincia' | 'regione';

export type RoofDataSource = 'google_solar' | 'mapbox_ai_fallback';

export type SubjectType = 'b2b' | 'b2c' | 'unknown';

export type RoofStatus =
  | 'discovered'
  | 'identified'
  | 'scored'
  | 'rendered'
  | 'outreach_sent'
  | 'engaged'
  | 'converted'
  | 'blacklisted'
  | 'rejected';

export type LeadScoreTier = 'hot' | 'warm' | 'cold' | 'rejected';

export type LeadStatus =
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

export type OutreachChannel = 'email' | 'postal';

export type InstallerFeedback =
  | 'qualified'
  | 'not_interested'
  | 'not_reachable'
  | 'contract_signed'
  | 'wrong_data';

export type CampaignStatus = 'pending' | 'sent' | 'delivered' | 'failed' | 'cancelled';

export type BlacklistReason =
  | 'user_optout'
  | 'manual'
  | 'regulatory'
  | 'bounce_hard'
  | 'complaint';
