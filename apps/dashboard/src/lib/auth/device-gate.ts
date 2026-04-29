/**
 * Device authorization gate — server-side library.
 *
 * Implements the lookup + auto-registration logic for the demo
 * device-limit feature (migration 0074). Used by the Next.js
 * middleware to decide, after Supabase auth has validated the
 * session, whether *this device* is allowed in.
 *
 * Cookie semantics
 *   `sld-dev` is an opaque server-issued token (HttpOnly, Secure,
 *   SameSite=Lax, 1-year TTL). Stable across logout/login on the
 *   same browser. Cleared by the user → falls back to soft
 *   fingerprint lookup so the same browser is still recognised.
 *
 * Soft fingerprint
 *   SHA256(user_agent || '|' || ip_subnet24). Lossy by design —
 *   identifies "the same browser on the same NAT" without
 *   needing client-side JS or canvas tricks.
 *
 * Decision tree
 *   1. cookie present + matches active row  → ALLOW, touch last_seen
 *   2. fingerprint matches active row       → ALLOW, reissue cookie, touch last_seen
 *   3. no match, free client slot           → REGISTER + ALLOW
 *   4. no match, all slots full             → BLOCK (caller redirects to /access-denied)
 *
 * Admin devices never expire and never count against the dynamic
 * client quota. They are pinned via `role='admin'` from the
 * /settings/devices admin page.
 */

// NOTE: this module runs inside the Next.js middleware (Edge runtime),
// which does NOT expose `node:crypto`. We use the global Web Crypto
// API instead — available in both Edge and Node runtimes.
import { createClient } from '@supabase/supabase-js';

function bytesToHex(bytes: Uint8Array): string {
  let out = '';
  for (let i = 0; i < bytes.length; i++) {
    out += bytes[i]!.toString(16).padStart(2, '0');
  }
  return out;
}

const COOKIE_NAME = 'sld-dev';
const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 365; // 1 year

export const DEVICE_COOKIE_NAME = COOKIE_NAME;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type DeviceGateDecision =
  | { kind: 'allow'; setCookie?: { name: string; value: string; options: CookieOptionsLite } }
  | { kind: 'block' };

interface CookieOptionsLite {
  httpOnly: true;
  secure: true;
  sameSite: 'lax';
  maxAge: number;
  path: '/';
}

interface DeviceRow {
  id: string;
  cookie_token: string;
  fingerprint_hash: string;
  role: 'admin' | 'client';
  revoked_at: string | null;
}

interface TenantDeviceConfig {
  enabled: boolean;
  max_total: number;
}

// ---------------------------------------------------------------------------
// Service-role Supabase client
//
// Middleware runs without a user session in DB context (we only know who they
// are *for the application*; Postgres RLS sees `service_role`). So we use a
// service-role client and gate by `tenant_id` explicitly in every query.
// ---------------------------------------------------------------------------

function getServiceClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!url || !key) {
    throw new Error(
      'device-gate: missing NEXT_PUBLIC_SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY',
    );
  }
  return createClient(url, key, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}

// ---------------------------------------------------------------------------
// Fingerprint helpers
// ---------------------------------------------------------------------------

/** SHA256(ua || '|' || ip_subnet) → hex. Async — Web Crypto digest is async. */
export async function softFingerprint(
  userAgent: string,
  ipSubnet: string,
): Promise<string> {
  const data = new TextEncoder().encode(`${userAgent}|${ipSubnet}`);
  const hashBuffer = await crypto.subtle.digest('SHA-256', data);
  return bytesToHex(new Uint8Array(hashBuffer));
}

/** Stable /24 bucket for IPv4 ("203.0.113.45" → "203.0.113.0/24"). IPv6
 *  collapses to /48 ("2001:db8:abcd::1" → "2001:db8:abcd::/48"). Best-effort. */
export function ipSubnet(ip: string | null | undefined): string {
  if (!ip) return 'unknown';
  // Trim port if present.
  const cleaned = (ip.split(',')[0] ?? '').trim().replace(/^::ffff:/, '');
  if (cleaned.includes(':')) {
    // IPv6 → /48
    const parts = cleaned.split(':').slice(0, 3);
    return `${parts.join(':')}::/48`;
  }
  const parts = cleaned.split('.');
  if (parts.length === 4) return `${parts[0]}.${parts[1]}.${parts[2]}.0/24`;
  return cleaned;
}

/** Friendly label from User-Agent — best-effort regex parsing. */
export function friendlyDeviceName(ua: string): string {
  const macMatch = ua.match(/Mac OS X ([\d_]+)/);
  const winMatch = ua.match(/Windows NT ([\d.]+)/);
  const linuxMatch = ua.includes('Linux');
  const iosMatch = ua.match(/iPhone OS ([\d_]+)/);
  const androidMatch = ua.match(/Android ([\d.]+)/);

  const browserMatch =
    ua.match(/Edg\/([\d.]+)/) ??
    ua.match(/Chrome\/([\d.]+)/) ??
    ua.match(/Firefox\/([\d.]+)/) ??
    ua.match(/Safari\/([\d.]+)/);
  const browser = ua.includes('Edg/')
    ? 'Edge'
    : ua.includes('Chrome/') && !ua.includes('Edg/')
      ? 'Chrome'
      : ua.includes('Firefox/')
        ? 'Firefox'
        : ua.includes('Safari/')
          ? 'Safari'
          : 'Browser';

  let os = 'Sconosciuto';
  if (iosMatch?.[1]) os = `iOS ${iosMatch[1].replace(/_/g, '.')}`;
  else if (androidMatch?.[1]) os = `Android ${androidMatch[1]}`;
  else if (macMatch?.[1]) os = `macOS ${macMatch[1].replace(/_/g, '.')}`;
  else if (winMatch) os = 'Windows';
  else if (linuxMatch) os = 'Linux';

  const browserVer = browserMatch?.[1]?.split('.')[0] ?? '';
  return `${browser}${browserVer ? ' ' + browserVer : ''} · ${os}`;
}

function newCookieToken(): string {
  const arr = new Uint8Array(32);
  crypto.getRandomValues(arr);
  return bytesToHex(arr);
}

function defaultCookieOptions(): CookieOptionsLite {
  return {
    httpOnly: true,
    secure: true,
    sameSite: 'lax',
    maxAge: COOKIE_MAX_AGE_SECONDS,
    path: '/',
  };
}

// ---------------------------------------------------------------------------
// Tenant lookup helpers
// ---------------------------------------------------------------------------

/** Lookup the tenant_id for a given user from tenant_members.
 *  Returns null if the user has no tenant yet (mid-signup). */
async function getTenantIdForUser(userId: string): Promise<string | null> {
  const sb = getServiceClient();
  const { data, error } = await sb
    .from('tenant_members')
    .select('tenant_id')
    .eq('user_id', userId)
    .limit(1)
    .maybeSingle();
  if (error || !data) return null;
  return (data as { tenant_id: string }).tenant_id;
}

async function getTenantDeviceConfig(
  tenantId: string,
): Promise<TenantDeviceConfig | null> {
  const sb = getServiceClient();
  const { data, error } = await sb
    .from('tenants')
    .select('demo_device_limit_enabled, demo_device_max_total')
    .eq('id', tenantId)
    .maybeSingle();
  if (error || !data) return null;
  const row = data as {
    demo_device_limit_enabled: boolean;
    demo_device_max_total: number;
  };
  return {
    enabled: Boolean(row.demo_device_limit_enabled),
    max_total: row.demo_device_max_total ?? 3,
  };
}

// ---------------------------------------------------------------------------
// Main entry
// ---------------------------------------------------------------------------

/**
 * Run the device gate for an authenticated request.
 *
 * @param userId        Supabase auth user id (already validated upstream)
 * @param userAgent     request User-Agent header
 * @param ip            client IP (from x-forwarded-for or x-real-ip)
 * @param cookieToken   value of the `sld-dev` cookie if present
 * @returns             allow/block decision; allow may include a setCookie
 */
export async function evaluateDeviceGate(args: {
  userId: string;
  userAgent: string;
  ip: string | null;
  cookieToken: string | null;
}): Promise<DeviceGateDecision> {
  const tenantId = await getTenantIdForUser(args.userId);
  if (!tenantId) {
    // No tenant yet → let the layout handle it (will redirect to /onboarding
    // or /no-tenant). Don't block here.
    return { kind: 'allow' };
  }

  const cfg = await getTenantDeviceConfig(tenantId);
  if (!cfg || !cfg.enabled) {
    // Gate disabled for this tenant → bypass.
    return { kind: 'allow' };
  }

  const sb = getServiceClient();
  const subnet = ipSubnet(args.ip);
  const fingerprint = await softFingerprint(args.userAgent, subnet);

  // ── 1. Cookie match ──────────────────────────────────────────────────
  if (args.cookieToken) {
    const { data } = await sb
      .from('tenant_authorized_devices')
      .select('id, cookie_token, fingerprint_hash, role, revoked_at')
      .eq('cookie_token', args.cookieToken)
      .eq('tenant_id', tenantId)
      .is('revoked_at', null)
      .maybeSingle();
    const row = data as DeviceRow | null;
    if (row) {
      // Touch last_seen + last_user_id (best effort).
      await sb
        .from('tenant_authorized_devices')
        .update({
          last_seen_at: new Date().toISOString(),
          last_user_id: args.userId,
          ip_subnet: subnet,
        })
        .eq('id', row.id);
      return { kind: 'allow' };
    }
  }

  // ── 2. Soft-fingerprint match ────────────────────────────────────────
  {
    const { data } = await sb
      .from('tenant_authorized_devices')
      .select('id, cookie_token, fingerprint_hash, role, revoked_at')
      .eq('fingerprint_hash', fingerprint)
      .eq('tenant_id', tenantId)
      .is('revoked_at', null)
      .maybeSingle();
    const row = data as DeviceRow | null;
    if (row) {
      // Reissue the cookie so future requests hit path 1 directly.
      await sb
        .from('tenant_authorized_devices')
        .update({
          last_seen_at: new Date().toISOString(),
          last_user_id: args.userId,
          ip_subnet: subnet,
        })
        .eq('id', row.id);
      return {
        kind: 'allow',
        setCookie: {
          name: COOKIE_NAME,
          value: row.cookie_token,
          options: defaultCookieOptions(),
        },
      };
    }
  }

  // ── 3. Capacity check + auto-register ────────────────────────────────
  const { count } = await sb
    .from('tenant_authorized_devices')
    .select('id', { count: 'exact', head: true })
    .eq('tenant_id', tenantId)
    .is('revoked_at', null);
  const activeTotal = count ?? 0;

  if (activeTotal >= cfg.max_total) {
    // All seats taken (admin + clients combined).
    return { kind: 'block' };
  }

  const newToken = newCookieToken();
  const display = friendlyDeviceName(args.userAgent);

  const { data: inserted, error: insertErr } = await sb
    .from('tenant_authorized_devices')
    .insert({
      tenant_id: tenantId,
      fingerprint_hash: fingerprint,
      cookie_token: newToken,
      role: 'client',
      display_name: display,
      user_agent: args.userAgent.slice(0, 500),
      ip_subnet: subnet,
      last_user_id: args.userId,
    })
    .select('id, cookie_token')
    .single();

  if (insertErr || !inserted) {
    // Race: another request may have grabbed the slot. Re-check.
    const { count: c2 } = await sb
      .from('tenant_authorized_devices')
      .select('id', { count: 'exact', head: true })
      .eq('tenant_id', tenantId)
      .is('revoked_at', null);
    if ((c2 ?? 0) >= cfg.max_total) return { kind: 'block' };
    // Otherwise allow this single request through; next refresh will
    // converge. We do not return setCookie because we have no row to
    // bind to — soft FP will pick it up on the next request.
    return { kind: 'allow' };
  }

  return {
    kind: 'allow',
    setCookie: {
      name: COOKIE_NAME,
      value: (inserted as { cookie_token: string }).cookie_token,
      options: defaultCookieOptions(),
    },
  };
}
