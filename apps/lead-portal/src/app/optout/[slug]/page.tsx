import { OptoutConfirm } from './OptoutConfirm';

type PageProps = {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ already?: string }>;
};

export default async function OptoutPage({ params, searchParams }: PageProps) {
  const { slug } = await params;
  const sp = await searchParams;
  const already = sp.already === '1';

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 p-6">
      <div className="w-full max-w-md rounded-lg bg-white p-8 shadow">
        <h1 className="text-2xl font-semibold">Non ricevere più comunicazioni</h1>
        <p className="mt-2 text-sm text-slate-600">
          {already
            ? 'Abbiamo già registrato la vostra richiesta. Non riceverete altre email da noi.'
            : "Cliccando 'Conferma' non riceverete più comunicazioni da noi, né da altri installatori collegati al circuito SolarLead."}
        </p>
        {!already ? <OptoutConfirm slug={slug} /> : null}
        <p className="mt-6 text-xs text-slate-500">
          Se pensi di ricevere ancora email dopo la conferma, scrivi a{' '}
          <a href="mailto:privacy@solarlead.it" className="underline">
            privacy@solarlead.it
          </a>
          .
        </p>
      </div>
    </main>
  );
}
