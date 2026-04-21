import { NextResponse, type NextRequest } from 'next/server';
import { createServerClient, type CookieOptions } from '@supabase/ssr';

type CookieToSet = { name: string; value: string; options?: CookieOptions };

export async function updateSession(request: NextRequest) {
  let supabaseResponse = NextResponse.next({ request });

  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet: CookieToSet[]) {
          cookiesToSet.forEach(({ name, value }) =>
            request.cookies.set(name, value),
          );
          supabaseResponse = NextResponse.next({ request });
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  const { data: { user } } = await supabase.auth.getUser();

  const pathname = request.nextUrl.pathname;
  const isAuthPage = pathname.startsWith('/login') || pathname === '/';
  const isProtected =
    pathname.startsWith('/leads') ||
    pathname.startsWith('/territories') ||
    pathname.startsWith('/campaigns') ||
    pathname.startsWith('/analytics') ||
    pathname.startsWith('/settings');

  // Unauthenticated → protect dashboard routes.
  if (!user && isProtected) {
    const url = request.nextUrl.clone();
    url.pathname = '/login';
    return NextResponse.redirect(url);
  }

  // NOTE: we intentionally do NOT redirect logged-in users away from
  // /login here. The dashboard layout checks `getCurrentTenantContext()`
  // which can return null for users who have a valid Supabase session but
  // no `tenant_members` row yet (e.g. mid-signup). If we redirect them
  // here, the layout's "no tenant → /login" and this "logged-in → /leads"
  // create an infinite loop. The login form itself handles post-login
  // navigation via router.push('/leads').

  return supabaseResponse;
}
