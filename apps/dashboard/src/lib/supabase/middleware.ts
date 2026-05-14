import { NextResponse, type NextRequest } from 'next/server';
import { createServerClient, type CookieOptions } from '@supabase/ssr';

import { DEVICE_COOKIE_NAME, evaluateDeviceGate } from '@/lib/auth/device-gate';

type CookieToSet = { name: string; value: string; options?: CookieOptions };

export async function updateSession(request: NextRequest) {
  // Forward the request pathname as a custom header so server components
  // (e.g. layouts) can read it via `headers()`. App Router intentionally
  // doesn't expose pathname server-side, so we surface it ourselves.
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set('x-pathname', request.nextUrl.pathname);

  let supabaseResponse = NextResponse.next({
    request: { headers: requestHeaders },
  });

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
          supabaseResponse = NextResponse.next({
            request: { headers: requestHeaders },
          });
          cookiesToSet.forEach(({ name, value, options }) =>
            supabaseResponse.cookies.set(name, value, options),
          );
        },
      },
    },
  );

  const { data: { user } } = await supabase.auth.getUser();

  const pathname = request.nextUrl.pathname;

  // Public paths that never require auth (and must never redirect-loop).
  const isPublicPath =
    pathname.startsWith('/login') ||
    pathname.startsWith('/signup') ||
    pathname.startsWith('/access-denied') ||
    pathname.startsWith('/signout') ||
    pathname.startsWith('/onboarding') ||
    pathname.startsWith('/no-tenant') ||
    pathname.startsWith('/api/');

  // Paths that require an authenticated Supabase session. We keep this
  // list explicit (rather than "everything not public") to avoid the
  // /login → layout infinite redirect loop documented above.
  const isProtected =
    !isPublicPath && (
      pathname.startsWith('/leads') ||
      pathname.startsWith('/territories') ||
      pathname.startsWith('/campaigns') ||
      pathname.startsWith('/settings') ||
      pathname.startsWith('/contatti') ||
      pathname.startsWith('/scoperta') ||
      pathname.startsWith('/email-templates') ||
      pathname === '/' ||
      pathname.startsWith('/dashboard')
    );

  // Unauthenticated → protect dashboard routes.
  if (!user && isProtected) {
    const url = request.nextUrl.clone();
    url.pathname = '/login';
    return NextResponse.redirect(url);
  }

  // ────────────────────────────────────────────────────────────────────────
  // Device-authorization gate (migration 0074).
  //
  // Runs for authenticated users on ALL non-public paths so no dashboard
  // route can be accessed from an unrecognised device. Tenants with the
  // gate disabled are short-circuited inside evaluateDeviceGate(). For
  // tenants with the gate enabled, the (max_total+1)-th device gets a
  // hard 'block' and is redirected to /access-denied.
  //
  // /access-denied is exempt so blocked users can still see the message;
  // /api/ and /onboarding are exempt for functional reasons.
  // ────────────────────────────────────────────────────────────────────────
  const isDeviceGateExempt = isPublicPath;

  if (user && !isDeviceGateExempt) {
    try {
      const ip =
        request.headers.get('x-forwarded-for') ??
        request.headers.get('x-real-ip') ??
        null;
      const ua = request.headers.get('user-agent') ?? '';
      const cookieToken = request.cookies.get(DEVICE_COOKIE_NAME)?.value ?? null;

      const decision = await evaluateDeviceGate({
        userId: user.id,
        userAgent: ua,
        ip,
        cookieToken,
      });

      if (decision.kind === 'block') {
        const url = request.nextUrl.clone();
        url.pathname = '/access-denied';
        url.search = '';
        return NextResponse.redirect(url);
      }

      if (decision.kind === 'allow' && decision.setCookie) {
        // Apply the new device cookie on the response that will continue
        // through to the protected route.
        supabaseResponse.cookies.set(
          decision.setCookie.name,
          decision.setCookie.value,
          decision.setCookie.options,
        );
      }
    } catch (err) {
      // Fail-open by design: a transient DB hiccup must not lock everyone
      // out. The error is logged so it surfaces in observability.
      console.error('device-gate: evaluation failed', err);
    }
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
