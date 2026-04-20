import { PageSkeleton } from '@/components/ui/page-skeleton';

/** Fallback skeleton for any dashboard route without its own loading.tsx */
export default function Loading() {
  return <PageSkeleton rows={4} />;
}
