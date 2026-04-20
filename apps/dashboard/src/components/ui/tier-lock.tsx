/**
 * Tier lock — renders its children when the tenant has access to
 * `feature`, otherwise shows an upgrade overlay with:
 *   - a lucchetto glyph
 *   - a headline naming the minimum tier required
 *   - a mailto CTA to contact ops for manual activation (no Stripe,
 *     by product decision).
 *
 * The component is server-side (no 'use client') — it runs during SSR
 * and the entire subtree only mounts when allowed, so client bundles
 * of pro-only features are never shipped to founding-tier users.
 */

import type { ReactNode } from 'react';

import {
  TIER_LABEL,
  canTenantUse,
  minimumTierFor,
  type CapabilityKey,
} from '@/lib/data/tier';
import { cn } from '@/lib/utils';
import type { TenantRow } from '@/types/db';

interface TierLockProps {
  feature: CapabilityKey;
  tenant: TenantRow;
  children: ReactNode;
  /** Optional short label shown in the overlay ("timeline live"). */
  featureLabel?: string;
  /** Override upgrade email for the CTA. Default = `upgrade@solarlead.it`. */
  upgradeEmail?: string;
  /** If true, renders a compact inline card instead of a full-height overlay. */
  inline?: boolean;
  className?: string;
}

export function TierLock({
  feature,
  tenant,
  children,
  featureLabel,
  upgradeEmail = 'upgrade@solarlead.it',
  inline = false,
  className,
}: TierLockProps) {
  if (canTenantUse(tenant, feature)) {
    return <>{children}</>;
  }

  const required = minimumTierFor(feature);
  const label = featureLabel ?? feature.replace(/_/g, ' ');
  const subject = encodeURIComponent(
    `Upgrade a ${TIER_LABEL[required]} — ${tenant.business_name}`,
  );
  const body = encodeURIComponent(
    `Ciao, vorrei attivare il piano ${TIER_LABEL[required]} per sbloccare "${label}".\n\nTenant: ${tenant.business_name} (${tenant.id})`,
  );

  return (
    <div
      className={cn(
        'relative overflow-hidden rounded-xl',
        inline
          ? 'border border-dashed border-outline-variant bg-surface-container-low p-6'
          : 'bg-surface-container-low p-10',
        className,
      )}
    >
      <div className="flex flex-col items-start gap-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-surface-container-highest text-on-surface-variant">
          {/* padlock */}
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="18"
            height="18"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
            <path d="M7 11V7a5 5 0 0 1 10 0v4" />
          </svg>
        </div>
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
            Disponibile con {TIER_LABEL[required]}
          </p>
          <h3 className="mt-1 font-headline text-xl font-bold tracking-tight text-on-surface">
            {label}
          </h3>
          <p className="mt-1 max-w-md text-sm text-on-surface-variant">
            Questa funzione richiede il piano{' '}
            <span className="font-semibold">{TIER_LABEL[required]}</span>. Il
            tuo piano attuale è{' '}
            <span className="font-semibold">{TIER_LABEL[tenant.tier]}</span>.
          </p>
        </div>
        <a
          href={`mailto:${upgradeEmail}?subject=${subject}&body=${body}`}
          className="mt-1 inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-semibold text-on-primary shadow-ambient-sm transition-colors hover:bg-primary/90"
        >
          Richiedi upgrade
        </a>
      </div>
    </div>
  );
}
