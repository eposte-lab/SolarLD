'use client';

/**
 * EmailReplyCta — secondary CTA next to the WhatsApp primary card.
 *
 * Sprint 8 Fase A.3. Builds a ``mailto:`` deep-link with subject and
 * body pre-filled so the reply lands in the operator's inbox already
 * threaded under the original outreach (the email subject mirrors the
 * dossier hero), and fires ``portal.email_reply_click`` — Fase C.1
 * weights this +35 on engagement_score (a reply email is high intent
 * even if the user never sends WhatsApp).
 *
 * Falls back to a disabled state if the tenant has no contact_email.
 */

import { Mail } from 'lucide-react';

import { postPortalEvent } from '@/lib/tracking';

type Props = {
  slug: string;
  contactEmail: string | null;
  tenantName: string;
  heroTitle: string;
  brandColor: string;
};

export function EmailReplyCta({
  slug,
  contactEmail,
  tenantName,
  heroTitle,
  brandColor,
}: Props) {
  if (!contactEmail) {
    return (
      <div className="bento p-6 text-center text-on-surface-muted">
        Email diretta non ancora configurata.
      </div>
    );
  }

  const subject = `Re: ${heroTitle}`;
  const body =
    `Buongiorno ${tenantName},\n\n` +
    `vorrei ricevere maggiori informazioni sul preventivo personalizzato ` +
    `che mi avete inviato.\n\n` +
    `Grazie,\n`;

  const href = `mailto:${encodeURIComponent(contactEmail)}` +
    `?subject=${encodeURIComponent(subject)}` +
    `&body=${encodeURIComponent(body)}`;

  const handleClick = () => {
    postPortalEvent(slug, 'portal.email_reply_click', {
      tenant_email: contactEmail,
    });
  };

  return (
    <a
      href={href}
      onClick={handleClick}
      className="bento group flex h-full flex-col justify-between gap-3 p-6 transition-shadow hover:shadow-ambient-md"
      data-portal-cta="email-reply"
    >
      <div className="flex items-center gap-3">
        <span
          aria-hidden
          className="inline-flex h-10 w-10 items-center justify-center rounded-full text-white"
          style={{ backgroundColor: brandColor }}
        >
          <Mail className="h-5 w-5" />
        </span>
        <div>
          <p className="editorial-eyebrow">Risposta diretta</p>
          <h3 className="font-headline text-lg font-semibold text-on-surface">
            Rispondi via email
          </h3>
        </div>
      </div>
      <p className="text-sm text-on-surface-variant">
        Apri il client mail con un messaggio già pronto: aggiungi le tue
        domande e invia. Riceverai risposta entro 24 ore lavorative.
      </p>
      <span
        className="text-sm font-semibold transition-colors group-hover:underline"
        style={{ color: brandColor }}
      >
        Apri client email →
      </span>
    </a>
  );
}
