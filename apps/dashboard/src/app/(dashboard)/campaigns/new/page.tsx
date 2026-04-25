/**
 * /campaigns/new — Create a new acquisition campaign.
 *
 * Simple form: name + description. The server action copies the
 * tenant's current module configs into the new campaign snapshot
 * (so the campaign starts with sensible defaults the user already
 * tuned in the wizard). Individual module overrides can be made
 * on /campaigns/[id] after creation.
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { getCurrentTenantContext } from '@/lib/data/tenant';

import { createCampaign } from '../_actions';

export default async function NewCampaignPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const { error } = await searchParams;

  const errorMsg: Record<string, string> = {
    missing_name: 'Inserisci un nome per la campagna.',
    api_unreachable: "Impossibile raggiungere l'API. Riprova tra qualche secondo.",
    create_failed: 'Errore nella creazione. Riprova.',
  };

  return (
    <div className="mx-auto max-w-xl space-y-6">
      {/* Header */}
      <header className="space-y-1">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          <Link href="/campaigns" className="hover:underline">
            Campagne
          </Link>
          {' · '}Nuova
        </p>
        <h1 className="font-headline text-3xl font-bold tracking-tighter">
          Nuova campagna
        </h1>
        <p className="text-sm text-on-surface-variant">
          La configurazione viene copiata dai tuoi moduli attuali. Potrai
          modificare ogni sezione dopo la creazione.
        </p>
      </header>

      {/* Error */}
      {error && errorMsg[error] && (
        <div
          role="alert"
          className="rounded-xl bg-error-container px-4 py-3 text-sm font-semibold text-on-error-container"
        >
          {errorMsg[error]}
        </div>
      )}

      {/* Form */}
      <form action={createCampaign} className="space-y-4">
        <div>
          <label
            htmlFor="name"
            className="mb-1 block text-xs font-semibold text-on-surface-variant"
          >
            Nome campagna <span className="text-error">*</span>
          </label>
          <input
            id="name"
            name="name"
            type="text"
            required
            autoFocus
            placeholder="es. Manifatturiero Nord Italia Q3 2026"
            className="w-full rounded-xl border border-outline-variant/60 bg-surface-container-lowest px-3 py-2.5 text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
        </div>

        <div>
          <label
            htmlFor="description"
            className="mb-1 block text-xs font-semibold text-on-surface-variant"
          >
            Descrizione
            <span className="ml-1 font-normal text-on-surface-variant/60">
              — opzionale
            </span>
          </label>
          <textarea
            id="description"
            name="description"
            rows={3}
            placeholder="Obiettivo della campagna, note operative, stagionalità…"
            className="w-full rounded-xl border border-outline-variant/60 bg-surface-container-lowest px-3 py-2.5 text-sm text-on-surface placeholder:text-on-surface-variant/50 focus:outline-none focus:ring-2 focus:ring-primary/40"
          />
        </div>

        <div className="flex items-center gap-3 pt-2">
          <button
            type="submit"
            className="rounded-xl bg-primary px-5 py-2.5 text-sm font-semibold text-on-primary shadow-ambient-sm"
          >
            Crea campagna
          </button>
          <Link
            href="/campaigns"
            className="rounded-xl border border-outline-variant/40 px-5 py-2.5 text-sm font-semibold text-on-surface hover:bg-surface-container-low"
          >
            Annulla
          </Link>
        </div>
      </form>
    </div>
  );
}
