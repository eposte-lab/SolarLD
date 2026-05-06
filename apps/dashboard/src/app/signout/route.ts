/**
 * /signout — server-side route that signs the user out of Supabase
 * and clears the device-gate cookie before redirecting to /login.
 *
 * Used as the escape hatch from /access-denied, /no-tenant, and any
 * "logout" link that needs hard cookie cleanup. Linking to this URL
 * from a plain `<a href>` works without client JS.
 */

import { NextResponse } from 'next/server';

import { createSupabaseServerClient } from '@/lib/supabase/server';

export async function GET(request: Request) {
  const sb = await createSupabaseServerClient();
  await sb.auth.signOut();

  const url = new URL('/login', request.url);
  const res = NextResponse.redirect(url);

  // IMPORTANT: we intentionally do NOT clear the sld-dev device cookie here.
  // The device slot in tenant_authorized_devices must remain occupied even
  // after logout — the intent is that once a physical device has been
  // registered it keeps its slot permanently (until an admin explicitly
  // revokes it from /settings/devices). Clearing the cookie would force
  // fingerprint-only recognition on the next login, which fails when the
  // user's IP has changed (e.g. office → home), causing them to be counted
  // as a new device and potentially blocked or double-registered.
  return res;
}

export const POST = GET;
