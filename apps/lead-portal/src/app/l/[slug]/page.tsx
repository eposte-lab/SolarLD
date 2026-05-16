import { permanentRedirect } from 'next/navigation';

/**
 * Legacy alias for `/dossier/[slug]`.
 *
 * Sprint 8 Fase A.1 — every email outreach.py sent before the path fix
 * (`/l/{slug}` → portale) emitted CTAs targeting this route. La pagina
 * del portale vive a `/dossier/[slug]` (ex `/lead/[slug]`), quindi senza
 * questo alias ogni vecchio link email tornerebbe 404.
 *
 * 308 permanent redirect preserva lo slug, mantiene funzionanti i CNAME
 * di tracking e segnala a client/proxy che l'URL canonico è cambiato.
 *
 * Il forward path emette `/dossier/...` direttamente (vedi
 * `apps/api/src/agents/outreach.py::_public_lead_url`), quindi questa
 * route scatta solo per email già inviate.
 */
type PageProps = { params: Promise<{ slug: string }> };

export default async function LegacyLeadAliasPage({ params }: PageProps) {
  const { slug } = await params;
  permanentRedirect(`/dossier/${encodeURIComponent(slug)}`);
}
