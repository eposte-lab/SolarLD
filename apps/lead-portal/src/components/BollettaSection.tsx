'use client';

/**
 * BollettaSection — client wrapper that owns the refresh key shared
 * between BillUploadCard and SavingsComparePanel.
 *
 * The lead page is a server component (it fetches the lead from the
 * API on render) but the upload card needs to drive re-fetches on the
 * client side. Wrapping both in this small client component keeps the
 * server page boundary clean.
 */

import { useState } from 'react';

import { BillUploadCard } from './BillUploadCard';
import { SavingsComparePanel } from './SavingsComparePanel';

type Props = {
  slug: string;
  brandColor: string;
};

export function BollettaSection({ slug, brandColor }: Props) {
  const [refreshKey, setRefreshKey] = useState(0);

  return (
    <div className="grid gap-4">
      <BillUploadCard
        slug={slug}
        brandColor={brandColor}
        onSaved={() => setRefreshKey((k) => k + 1)}
      />
      <SavingsComparePanel
        slug={slug}
        refreshKey={refreshKey}
        brandColor={brandColor}
      />
    </div>
  );
}
