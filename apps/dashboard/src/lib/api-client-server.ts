/**
 * Server-only FastAPI fetch wrapper.
 *
 * Mirrors `apiFetch` from `./api-client` but reads the Supabase JWT from
 * the SSR cookie (`createSupabaseServerClient`) instead of the browser
 * session. Use this from React Server Components and Server Actions —
 * never from `'use client'` files (the `'server-only'` marker will throw
 * a build error if you do).
 *
 * The split exists because `next/headers` (transitively imported by the
 * SSR Supabase helper) can't be bundled into the client. Keeping a
 * separate entry point for server-side API calls means client components
 * can still import the original `./api-client` without dragging server
 * modules into the browser bundle.
 */

import 'server-only';

import { createSupabaseServerClient } from './supabase/server';
import { ApiError, API_URL } from './api-client';

async function getServerAuthHeader(): Promise<Record<string, string>> {
  const supabase = await createSupabaseServerClient();
  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) return {};
  return { Authorization: `Bearer ${session.access_token}` };
}

export async function apiFetchServer<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const auth = await getServerAuthHeader();
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...auth,
      ...(init.headers ?? {}),
    },
    // RSC fetch defaults: no cache, since these endpoints are tenant-scoped
    // and small. The page itself is `dynamic = 'force-dynamic'` upstream.
    cache: 'no-store',
  });

  if (!res.ok) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      body = await res.text();
    }
    let detail: string | null = null;
    if (body != null && typeof body === 'object' && 'detail' in (body as object)) {
      const raw = (body as Record<string, unknown>).detail;
      if (typeof raw === 'string' && raw.trim()) {
        detail = raw;
      } else if (Array.isArray(raw)) {
        detail = 'Richiesta non valida.';
      } else if (raw && typeof raw === 'object') {
        detail = 'Operazione non riuscita.';
      }
    }
    const fallback =
      res.status >= 500
        ? 'Errore del servizio. Riprova tra qualche minuto.'
        : `Operazione non riuscita (codice ${res.status}).`;
    throw new ApiError(detail ?? fallback, res.status, body);
  }

  if (res.status === 204) return null as T;
  return (await res.json()) as T;
}
