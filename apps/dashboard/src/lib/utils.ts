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
