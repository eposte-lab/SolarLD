/**
 * Email Templates — client-side bindings for /v1/email-templates.
 *
 * Custom HTML email templates for generic_outreach campaigns.
 * The operator writes HTML with Jinja2-style {{ variable }} placeholders;
 * the OutreachAgent renders it with real lead data at send time.
 */
import { api } from '@/lib/api-client';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface EmailTemplate {
  id: string;
  tenant_id: string;
  name: string;
  subject: string;
  html: string;
  plain_text: string | null;
  variables_used: string[];
  created_at: string;
  updated_at: string;
}

/** Row returned by the list endpoint (no html body for performance). */
export interface EmailTemplateRow {
  id: string;
  name: string;
  subject: string;
  variables_used: string[];
  created_at: string;
  updated_at: string;
}

export interface TemplateVariable {
  slug: string;
  label: string;
  example: string;
}

export interface CreateTemplateInput {
  name: string;
  subject: string;
  html: string;
  plain_text?: string;
}

export interface UpdateTemplateInput {
  name?: string;
  subject?: string;
  html?: string;
  plain_text?: string;
}

export interface ValidationResult {
  valid: boolean;
  missing_required: string[];
  variables_found: string[];
}

// ---------------------------------------------------------------------------
// API surface
// ---------------------------------------------------------------------------

export async function listEmailTemplates(): Promise<{
  items: EmailTemplateRow[];
  count: number;
}> {
  return api.get('/v1/email-templates');
}

export async function getEmailTemplate(id: string): Promise<EmailTemplate> {
  return api.get(`/v1/email-templates/${id}`);
}

export async function createEmailTemplate(
  input: CreateTemplateInput,
): Promise<EmailTemplate> {
  return api.post('/v1/email-templates', input);
}

export async function updateEmailTemplate(
  id: string,
  input: UpdateTemplateInput,
): Promise<EmailTemplate> {
  return api.patch(`/v1/email-templates/${id}`, input);
}

export async function deleteEmailTemplate(id: string): Promise<void> {
  await api.delete(`/v1/email-templates/${id}`);
}

export async function previewEmailTemplate(
  id: string,
): Promise<{ html: string; subject: string }> {
  return api.post(`/v1/email-templates/${id}/preview`, {});
}

export async function validateEmailTemplate(html: string): Promise<ValidationResult> {
  return api.post('/v1/email-templates/validate', { html });
}

export async function listTemplateVariables(): Promise<{
  variables: TemplateVariable[];
  required: string[];
}> {
  return api.get('/v1/email-templates/variables');
}

// ---------------------------------------------------------------------------
// AI variant generation (Phase 4 — autonomous A/B loop)
// ---------------------------------------------------------------------------

export interface AiVariant {
  subject: string;
  html: string;
  angle: string;
  /** GDPR variables Haiku may have stripped. Empty array = safe to use. */
  missing_required: string[];
  valid: boolean;
}

export interface GenerateVariantsResponse {
  ok: boolean;
  count: number;
  variants: AiVariant[];
}

export async function generateTemplateVariants(
  templateId: string,
  nVariants = 2,
): Promise<GenerateVariantsResponse> {
  return api.post(`/v1/email-templates/${templateId}/generate-variants`, {
    n_variants: nVariants,
  });
}

// ---------------------------------------------------------------------------
// Assign template to a prospect list
// ---------------------------------------------------------------------------

/** Patch prospect_list.email_template_id via the prospector route. */
export async function assignTemplateToList(
  listId: string,
  emailTemplateId: string | null,
): Promise<void> {
  await api.patch(`/v1/prospector/lists/${listId}`, {
    email_template_id: emailTemplateId,
  });
}
