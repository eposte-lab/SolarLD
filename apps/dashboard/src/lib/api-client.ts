/**
 * Typed fetch wrapper for the FastAPI backend.
 * Auto-attaches the Supabase JWT from the browser session.
 */
import { createBrowserClient } from './supabase/client';

export const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public body?: unknown,
  ) {
    super(message);
  }
}

async function getAuthHeader(): Promise<Record<string, string>> {
  if (typeof window === 'undefined') return {};
  const supabase = createBrowserClient();
  const { data: { session } } = await supabase.auth.getSession();
  if (!session?.access_token) return {};
  return { Authorization: `Bearer ${session.access_token}` };
}

export async function apiFetch<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const auth = await getAuthHeader();
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...auth,
      ...(init.headers ?? {}),
    },
  });

  if (!res.ok) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      body = await res.text();
    }
    // Extract the FastAPI `detail` for the user-visible message.
    //
    // FastAPI returns three shapes for `detail`:
    //   1. plain string (HTTPException(detail="..."))         → use as-is
    //   2. structured dict ({"code": "...", "params": {...}}) → not a user
    //      string; render a generic message and keep the body for the caller
    //   3. validation array ([{"loc":[...],"msg":"...",...}]) → same — never
    //      render `[object Object]` or raw JSON to the user
    //
    // Anything that isn't plainly a string falls back to a generic Italian
    // message; the structured body is still attached to ApiError for callers
    // that want to inspect codes (tier gate, budget exceeded, etc.).
    let detail: string | null = null;
    if (body != null && typeof body === 'object' && 'detail' in (body as object)) {
      const raw = (body as Record<string, unknown>).detail;
      if (typeof raw === 'string' && raw.trim()) {
        detail = raw;
      } else if (Array.isArray(raw)) {
        detail = 'Richiesta non valida. Controlla i campi del modulo e riprova.';
      } else if (raw && typeof raw === 'object') {
        // structured error object — caller can read it via .body
        detail = 'Operazione non riuscita. Riprova tra qualche minuto.';
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

export const api = {
  get: <T>(path: string) => apiFetch<T>(path),
  post: <T>(path: string, body: unknown) =>
    apiFetch<T>(path, { method: 'POST', body: JSON.stringify(body) }),
  put: <T>(path: string, body: unknown) =>
    apiFetch<T>(path, { method: 'PUT', body: JSON.stringify(body) }),
  patch: <T>(path: string, body: unknown) =>
    apiFetch<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  delete: <T>(path: string) => apiFetch<T>(path, { method: 'DELETE' }),
};

/** Alias for backwards-compatibility with components that import apiClient. */
export const apiClient = api;
