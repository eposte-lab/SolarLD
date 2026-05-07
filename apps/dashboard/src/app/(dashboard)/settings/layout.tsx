/**
 * Settings hub layout — pass-through.
 *
 * Per-section gating for demo tenants is handled inside settings/page.tsx
 * (ModulesCard / IntegrationsCard receive `isDemo` and lock writable
 * fields with "Configurato in onboarding" tooltips). The layout used
 * to redirect every demo tenant to /leads, which contradicted the
 * page-level demo-aware UI and made the page unreachable.
 */
export default async function SettingsLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <>{children}</>;
}
