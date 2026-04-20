'use client';

/**
 * Thin client wrapper so the server page can mount LeadGdprActions
 * and handle the post-delete redirect via useRouter without making
 * the whole page a client component.
 */

import { useRouter } from 'next/navigation';

import { LeadGdprActions } from '@/components/lead-gdpr-actions';

export function LeadGdprActionsWrapper({
  leadId,
  leadName,
}: {
  leadId: string;
  leadName: string;
}) {
  const router = useRouter();
  return (
    <LeadGdprActions
      leadId={leadId}
      leadName={leadName}
      onDeleted={() => router.push('/leads')}
    />
  );
}
