import { NextResponse, type NextRequest } from 'next/server';
import { createServerClient, type CookieOptions } from '@supabase/ssr';

import { DEVICE_COOKIE_NAME, evaluateDeviceGate } from '@/lib/auth/device-gate';

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

  // ────────────────────────────────────────────────────────────────────────
  // Device-authorization gate (migration 0074).
  //
  // Runs only for authenticated users on protected paths. Tenants with the
  // gate disabled (the vast majority) are short-circuited inside
  // evaluateDeviceGate(). For demo tenants with the gate enabled the
  // 4th device hits a hard 'block' and is redirected to /access-denied.
  //
  // The /access-denied path itself is excluded so the user can still see
  // the message; /api/auth/* is excluded so revoke/promote actions can run.
  // ────────────────────────────────────────────────────────────────────────
  const isDeviceGateExempt =
    pathname.startsWith('/access-denied') ||
    pathname.startsWith('/api/auth/') ||
    pathname.startsWith('/onboarding') ||
    pathname.startsWith('/no-tenant');

  if (user && isProtected && !isDeviceGateExempt) {
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
