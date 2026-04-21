/**
 * `/settings/modules` — overview grid of all 5 modules.
 *
 * Shows a tile per module with its current active/inactive state +
 * last-edited timestamp. Clicking a tile navigates to the dedicated
 * edit page at `/settings/modules/[key]`.
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { getCurrentTenantContext } from '@/lib/data/tenant';
import { getModulesForTenant } from '@/lib/data/modules.server';
import {
  MODULE_DESCRIPTIONS,
  MODULE_LABELS,
} from '@/types/modules';

export default async function ModulesIndexPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const modules = await getModulesForTenant(ctx.tenant.id);

  return (
    <div className="space-y-6">
      <header>
        <h1 className="font-headline text-3xl font-bold tracking-tight text-on-surface">
          Moduli
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-on-surface-variant">
          Ogni modulo configura una parte della pipeline in modo
          indipendente. Disattiva un modulo per congelarne la
          configurazione senza perdere i valori salvati.
        </p>
      </header>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        {modules.map((m) => (
          <Link
            key={m.module_key}
            href={{ pathname: `/settings/modules/${m.module_key}` }}
            className="group block rounded-2xl border border-outline-variant/30 bg-surface-container-low p-5 transition-shadow hover:shadow-ambient-md"
          >
            <div className="flex items-start justify-between">
              <h2 className="font-headline text-lg font-semibold text-on-surface">
                {MODULE_LABELS[m.module_key]}
              </h2>
              <span
                className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-widest ${
                  m.active
                    ? 'bg-primary/20 text-primary'
                    : 'bg-surface-container-high text-on-surface-variant'
                }`}
              >
                {m.active ? 'Attivo' : 'Disattivo'}
              </span>
            </div>
            <p className="mt-2 text-sm text-on-surface-variant">
              {MODULE_DESCRIPTIONS[m.module_key]}
            </p>
            {m.updated_at && (
              <p className="mt-3 text-[11px] text-on-surface-variant">
                Ultima modifica: {new Date(m.updated_at).toLocaleString('it-IT')}
              </p>
            )}
          </Link>
        ))}
      </div>
    </div>
  );
}
