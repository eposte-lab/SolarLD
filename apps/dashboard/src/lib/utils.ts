import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatEur(cents: number): string {
  return new Intl.NumberFormat('it-IT', {
    style: 'currency',
    currency: 'EUR',
    minimumFractionDigits: 0,
  }).format(cents / 100);
}

export function formatKwh(value: number): string {
  return new Intl.NumberFormat('it-IT').format(Math.round(value)) + ' kWh';
}

export function formatKwp(value: number): string {
  return new Intl.NumberFormat('it-IT', { maximumFractionDigits: 1 }).format(value) + ' kWp';
}

export function formatEurPlain(eur: number | null | undefined): string {
  if (eur == null) return '—';
  return new Intl.NumberFormat('it-IT', {
    style: 'currency',
    currency: 'EUR',
    maximumFractionDigits: 0,
  }).format(eur);
}

export function formatNumber(value: number | null | undefined): string {
  if (value == null) return '—';
  return new Intl.NumberFormat('it-IT').format(value);
}

/** Short Italian date: "14 apr 2026". Null-safe. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return '—';
  return new Intl.DateTimeFormat('it-IT', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(new Date(iso));
}

/**
 * Relative Italian time like "3 giorni fa", "ieri", "2h fa".
 * Null-safe; granularity floors at 1 minute.
 */
export function relativeTime(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return '—';
  const diffMin = Math.round((now.getTime() - new Date(iso).getTime()) / 60_000);
  if (diffMin < 1) return 'ora';
  if (diffMin < 60) return `${diffMin} min fa`;
  const diffH = Math.round(diffMin / 60);
  if (diffH < 24) return `${diffH}h fa`;
  const diffD = Math.round(diffH / 24);
  if (diffD === 1) return 'ieri';
  if (diffD < 30) return `${diffD} giorni fa`;
  const diffMo = Math.round(diffD / 30);
  if (diffMo < 12) return `${diffMo} mesi fa`;
  return `${Math.round(diffMo / 12)} anni fa`;
}

/** Days since a given ISO timestamp. Null-safe. */
export function daysSince(iso: string | null | undefined, now = new Date()): number | null {
  if (!iso) return null;
  return Math.floor((now.getTime() - new Date(iso).getTime()) / (24 * 60 * 60 * 1000));
}

/** Percentage 0-1 → "42%" string. */
export function formatPercent(value: number, digits = 0): string {
  return `${(value * 100).toFixed(digits)}%`;
}
