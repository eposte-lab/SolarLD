/**
 * DossierExpired — pagina mostrata quando il link del dossier è scaduto.
 *
 * Il dossier è un link pubblico (slug non indovinabile, noindex). Per
 * limitare la finestra di esposizione dei dati il link scade dopo un
 * numero di giorni dall'invio (vedi `DOSSIER_TTL_DAYS` in
 * `app/dossier/[slug]/page.tsx`). Oltre quella soglia il prospect vede
 * questa pagina invece del dossier.
 */

export function DossierExpired({
  tenantName,
  brandColor,
  contactEmail,
}: {
  tenantName: string;
  brandColor: string;
  contactEmail: string | null;
}) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-surface px-6">
      <div className="max-w-md text-center">
        <div
          className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-2xl"
          style={{ backgroundColor: `${brandColor}1A`, color: brandColor }}
        >
          <svg
            width="26"
            height="26"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden
          >
            <circle cx="12" cy="12" r="9" />
            <path d="M12 7v5l3 2" />
          </svg>
        </div>
        <h1 className="font-headline text-2xl font-semibold text-on-surface">
          Questo dossier non è più disponibile
        </h1>
        <p className="mt-3 text-sm leading-relaxed text-on-surface-variant">
          Il link di questa proposta personalizzata è scaduto. Per ricevere
          un dossier aggiornato o maggiori informazioni, contatta
          direttamente {tenantName}.
        </p>
        {contactEmail ? (
          <a
            href={`mailto:${contactEmail}`}
            className="mt-6 inline-flex rounded-xl px-5 py-2.5 text-sm font-semibold text-white"
            style={{ backgroundColor: brandColor }}
          >
            Contatta {tenantName}
          </a>
        ) : null}
      </div>
    </main>
  );
}
