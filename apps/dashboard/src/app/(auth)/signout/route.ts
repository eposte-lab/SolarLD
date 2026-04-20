import { type NextRequest, NextResponse } from 'next/server';
import { createSupabaseServerClient } from '@/lib/supabase/server';

/**
 * GET /signout — signs the user out via Supabase SSR and redirects to
 * /login. Works in dev and production (uses the request's own origin).
 * Usage: navigate to http://localhost:3000/signout
 */
export async function GET(request: NextRequest) {
  const supabase = await createSupabaseServerClient();
  await supabase.auth.signOut();
  const loginUrl = new URL('/login', request.url);
  return NextResponse.redirect(loginUrl);
}
