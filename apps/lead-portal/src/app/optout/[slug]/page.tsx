import { OptoutConfirm } from './OptoutConfirm';
import { fetchPublicLead } from '@/lib/api';

type PageProps = {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ already?: string }>;
};

export default async function OptoutPage({ params, searchParams }: PageProps) {
  const { slug } = await params;
  const sp = await searchParams;
  const already = sp.already === '1';

  // Intesta la pagina al tenant (Titolare del trattamento). Se il lead
  // ha già fatto opt-out l'API risponde 410 → si ricade su un testo
  // generico.
  const res = await fetchPublicLead(slug);
  const tenant = res.kind === 'ok' ? res.lead.tenant : null;
  const sender = tenant?.legal_name || tenant?.business_name || null;
  const privacyEmail = tenant?.contact_email || 'privacy@solarlead.it';

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 p-6">
      <div className="w-full max-w-md rounded-lg bg-white p-8 shadow">
        <h1 className="text-2xl font-semibold">Non ricevere più comunicazioni</h1>
        <p className="mt-2 text-sm text-slate-600">
          {already
            ? `Abbiamo già registrato la vostra richiesta. Non riceverete altre email${
                sender ? ` da ${sender}` : ' da noi'
              }.`
            : `Cliccando 'Conferma' non riceverete più comunicazioni${
                sender ? ` da ${sender}` : ' da noi'
              }.`}
        </p>
        {!already ? <OptoutConfirm slug={slug} /> : null}
        <p className="mt-6 text-xs text-slate-500">
          Se pensi di ricevere ancora email dopo la conferma, scrivi a{' '}
          <a href={`mailto:${privacyEmail}`} className="underline">
            {privacyEmail}
          </a>
          .
        </p>
      </div>
    </main>
  );
}
