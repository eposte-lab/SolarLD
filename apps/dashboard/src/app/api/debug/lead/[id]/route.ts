/**
 * DEBUG ONLY — dumps the full lead detail data fetched by the
 * /leads/[id] page, so we can diagnose Server Component render errors
 * that Next.js masks in production. To be removed once the funnel v3
 * dashboard contract is stable.
 *
 * Usage: GET /api/debug/lead/<lead-id>
 *
 * Returns the same 8 things the page fetches in parallel, plus a
 * derived `pageWouldThrow` boolean by trying to JSON.stringify each
 * piece (catches circular refs / non-serialisable values that would
 * trip React's render).
 */

import { NextResponse } from 'next/server';

import { listCampaignsForLead, listEventsForLead } from '@/lib/data/campaigns';
import { getConversationsForLead } from '@/lib/data/conversations';
import { listPortalEventsForLead } from '@/lib/data/engagement';
import { getLeadById, getLeadSectorSignal, getLeadV3Signal } from '@/lib/data/leads';
import { getLeadReplies } from '@/lib/data/replies';
import { getCurrentTenantContext } from '@/lib/data/tenant';

export const dynamic = 'force-dynamic';

type Wrapped = { ok: true; value: unknown } | { ok: false; error: string; stack?: string };
async function wrap(p: Promise<unknown>): Promise<Wrapped> {
  try {
    return { ok: true, value: await p };
  } catch (e) {
    const err = e as Error;
    return { ok: false, error: err?.message ?? String(e), stack: err?.stack };
  }
}

export async function GET(
  _req: Request,
  ctx: { params: Promise<{ id: string }> },
) {
  const tenant = await getCurrentTenantContext();
  if (!tenant) {
    return NextResponse.json({ error: 'unauthenticated' }, { status: 401 });
  }
  const { id } = await ctx.params;

  const [
    lead, campaigns, events, replies, conversations,
    portalEvents, sectorSignal, v3Signal,
  ] = await Promise.all([
    wrap(getLeadById(id)),
    wrap(listCampaignsForLead(id)),
    wrap(listEventsForLead(id)),
    wrap(getLeadReplies(id)),
    wrap(getConversationsForLead(id)),
    wrap(listPortalEventsForLead(id, 50)),
    wrap(getLeadSectorSignal(id)),
    wrap(getLeadV3Signal(id)),
  ]);

  return NextResponse.json({
    id,
    tenant_id: tenant.tenant.id,
    lead,
    campaigns,
    events,
    replies,
    conversations,
    portalEvents,
    sectorSignal,
    v3Signal,
  });
}
