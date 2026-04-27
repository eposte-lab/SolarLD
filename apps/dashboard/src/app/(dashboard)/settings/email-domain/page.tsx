/**
 * /settings/email-domain — LEGACY redirect.
 *
 * The single-domain setup has been superseded by the multi-domain hub at
 * /settings/email-domains (Sprint 6.2). This file permanently redirects so
 * that any bookmark or external link still lands on the right page.
 */

import { permanentRedirect } from 'next/navigation';

export default function LegacyEmailDomainPage() {
  permanentRedirect('/settings/email-domains');
}
