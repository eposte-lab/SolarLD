import { permanentRedirect } from 'next/navigation';

/**
 * Legacy alias for `/lead/[slug]`.
 *
 * Sprint 8 Fase A.1 — every email outreach.py sent before the path fix
 * (`/l/{slug}` → `/lead/{slug}`) emitted CTAs targeting this route. The
 * Next.js portal page actually lives at `/lead/[slug]`, so without this
 * alias every old email link returns 404.
 *
 * 308 permanent redirect preserves the slug, keeps tracking host CNAMEs
 * working, and signals to clients/proxies that the canonical URL has
 * moved — they'll prefer `/lead/...` on subsequent fetches if cached.
 *
 * The forward path emits `/lead/...` directly (see
 * `apps/api/src/agents/outreach.py::_public_lead_url`), so this route
 * should fire only for already-sent emails.
 */
type PageProps = { params: Promise<{ slug: string }> };

export default async function LegacyLeadAliasPage({ params }: PageProps) {
  const { slug } = await params;
  permanentRedirect(`/lead/${encodeURIComponent(slug)}`);
}
