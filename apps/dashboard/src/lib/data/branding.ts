/**
 * Server-side data accessors for branding & email domain (Part B.13).
 *
 * `getDomainStatus` calls the FastAPI endpoint server-side so the
 * email-domain page can pre-populate the DNS records table on first SSR
 * paint. Errors are swallowed so the page degrades gracefully when
 * Resend is unreachable.
 */

import { cookies } from 'next/headers';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export interface DnsRecord {
  type: string;
  name: string;
  value: string;
  priority: number | null;
  ttl: number | null;
  status: string;
}

export interface DomainStatusResponse {
  domain_id: string;
  domain: string;
  status: string;
  dns_records: DnsRecord[];
  created_at: string | null;
}

/**
 * Fetch the current domain verification status from the API.
 * Returns null when no domain is configured (404) or on network error.
 *
 * Must be called from a Server Component — reads the auth cookie.
 */
export async function getDomainStatus(): Promise<DomainStatusResponse | null> {
  // Server-side auth: read the Supabase session cookie so we can
  // attach the bearer token without going through the browser client.
  const cookieStore = await cookies();
  const supabaseAuthKey = [...cookieStore.getAll()].find(
    (c) =>
      c.name.startsWith('sb-') &&
      (c.name.endsWith('-auth-token') || c.name.endsWith('-auth-token.0')),
  );

  let token: string | null = null;
  if (supabaseAuthKey) {
    try {
      const parsed = JSON.parse(
        decodeURIComponent(supabaseAuthKey.value),
      ) as { access_token?: string } | string[];
      if (Array.isArray(parsed)) {
        const inner = JSON.parse(parsed[0] ?? '{}') as {
          access_token?: string;
        };
        token = inner.access_token ?? null;
      } else {
        token = parsed.access_token ?? null;
      }
    } catch {
      // cookie format unrecognised — proceed without auth
    }
  }

  if (!token) return null;

  try {
    const res = await fetch(`${API_URL}/v1/branding/domain/status`, {
      headers: { Authorization: `Bearer ${token}` },
      next: { revalidate: 0 },   // never cache — status changes frequently
    });
    if (res.status === 404) return null;
    if (!res.ok) return null;
    return (await res.json()) as DomainStatusResponse;
  } catch {
    return null;
  }
}
