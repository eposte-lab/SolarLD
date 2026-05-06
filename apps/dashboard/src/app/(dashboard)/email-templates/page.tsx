/**
 * /email-templates — Gestione template email personalizzati
 *
 * Pagina per creare e modificare template HTML custom per campagne
 * generic_outreach (es. amministratori condominio, studi dentistici, ecc.).
 * I template vengono poi associati alle liste in /scoperta.
 */

import { redirect } from 'next/navigation';

import { getCurrentTenantContext } from '@/lib/data/tenant';
import { EmailTemplatesClient } from './page-client';

export const dynamic = 'force-dynamic';

export default async function EmailTemplatesPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  return <EmailTemplatesClient />;
}
