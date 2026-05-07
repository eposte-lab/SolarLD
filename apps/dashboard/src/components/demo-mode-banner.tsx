/**
 * DemoModeBanner — top-of-page strip shown on tenants where
 * `outreach_blocked=true`. Makes it visually obvious that nothing the
 * operator clicks will ever reach a real prospect's inbox, and points
 * to the per-lead test-send form for verifying the flow end-to-end.
 *
 * Server component (no client state) — keeps it cheap to render on
 * every dashboard page that wants to surface the warning.
 */

interface Props {
  tenantName?: string | null;
}

export function DemoModeBanner({ tenantName }: Props) {
  return (
    <div className="rounded-xl bg-amber-50 px-4 py-3 ring-1 ring-amber-200">
      <p className="text-xs font-semibold uppercase tracking-wider text-amber-900">
        ★ Account demo · invio email reali disattivato
        {tenantName ? ` (${tenantName})` : ''}
      </p>
      <p className="mt-1 text-xs leading-relaxed text-amber-800">
        Su questo tenant il kill-switch dell&apos;outreach è attivo:
        nessuna email partirà mai verso il prospect reale, qualunque
        bottone tu prema. Per provare il flusso completo di invio (con
        rendering, ROI e link al portale) apri un lead e usa il form
        &laquo;Invia email di test&raquo; — riceverai la stessa email
        sulla tua casella personale.
      </p>
    </div>
  );
}
