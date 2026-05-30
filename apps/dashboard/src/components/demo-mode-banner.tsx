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
    <div className="flex items-start gap-3 rounded-xl bg-surface-container-high px-4 py-3 ring-1 ring-warning/25">
      <span
        aria-hidden
        className="mt-0.5 inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-warning/15 text-warning"
      >
        <svg width="11" height="11" viewBox="0 0 16 16" fill="currentColor">
          <path d="M8 1.5 15 14H1L8 1.5Zm0 3.6L3.6 12.8h8.8L8 5.1Zm-.8 2.2h1.6v2.9H7.2V7.3Zm0 3.6h1.6v1.4H7.2v-1.4Z" />
        </svg>
      </span>
      <div className="min-w-0">
        <p className="text-xs font-semibold uppercase tracking-wider text-on-surface">
          Account demo · invio email reali disattivato
          {tenantName ? ` (${tenantName})` : ''}
        </p>
        <p className="mt-1 text-xs leading-relaxed text-on-surface-variant">
          Il kill-switch dell&apos;outreach è attivo: nessuna email
          partirà verso un prospect reale. Per provare il flusso completo
          apri un lead e usa &laquo;Invia email di test&raquo; — la stessa
          email arriverà sulla tua casella.
        </p>
      </div>
    </div>
  );
}
