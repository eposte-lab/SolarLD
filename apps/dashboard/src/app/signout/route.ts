/**
 * /signout — server-side route that signs the user out of Supabase
 * and clears the device-gate cookie before redirecting to /login.
 *
 * Used as the escape hatch from /access-denied, /no-tenant, and any
 * "logout" link that needs hard cookie cleanup. Linking to this URL
 * from a plain `<a href>` works without client JS.
 */

import { NextResponse } from 'next/server';

import { DEVICE_COOKIE_NAME } from '@/lib/auth/device-gate';
import { createSupabaseServerClient } from '@/lib/supabase/server';

export async function GET(request: Request) {
  const sb = await createSupabaseServerClient();
  await sb.auth.signOut();

  const url = new URL('/login', request.url);
  const res = NextResponse.redirect(url);

  // Best-effort: clear the device cookie too. The Supabase client already
  // wiped its own session cookies via signOut().
  res.cookies.set(DEVICE_COOKIE_NAME, '', {
    httpOnly: true,
    secure: true,
    sameSite: 'lax',
    maxAge: 0,
    path: '/',
  });

  return res;
}

export const POST = GET;
