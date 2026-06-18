'use client';

/**
 * SearchBox — a URL-driven, debounced search input.
 *
 * Writes the query to a URL param (default `q`) and lets the server component
 * re-fetch with the filter applied (so it searches the WHOLE dataset, not just
 * the rows already on the page). Preserves every other query param — filter
 * chips, tabs — and resets `page` to 1 on a new query. Used on /contatti and
 * /invii to find a business by name (+ VAT / city).
 */

import { Search, X } from 'lucide-react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

export function SearchBox({
  placeholder = 'Cerca…',
  paramName = 'q',
  className = '',
  debounceMs = 350,
}: {
  placeholder?: string;
  paramName?: string;
  className?: string;
  debounceMs?: number;
}) {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const current = sp.get(paramName) ?? '';
  const [value, setValue] = useState(current);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Keep the field in sync when the URL changes from elsewhere (back button,
  // a reset link, a filter chip that drops `q`).
  useEffect(() => {
    setValue(current);
  }, [current]);

  function navigate(next: string) {
    const params = new URLSearchParams(sp.toString());
    const trimmed = next.trim();
    if (trimmed) params.set(paramName, trimmed);
    else params.delete(paramName);
    params.delete('page'); // a new query starts from page 1
    const qs = params.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }

  function onChange(next: string) {
    setValue(next);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => navigate(next), debounceMs);
  }

  function commitNow(next: string) {
    if (timer.current) clearTimeout(timer.current);
    navigate(next);
  }

  return (
    <div className={`relative w-full max-w-xs ${className}`}>
      <Search
        size={15}
        strokeWidth={2.25}
        className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-on-surface-variant"
        aria-hidden
      />
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') commitNow(value);
        }}
        placeholder={placeholder}
        aria-label={placeholder}
        className="w-full rounded-lg bg-surface-container-highest py-2 pl-9 pr-8 text-sm text-on-surface placeholder:text-on-surface-variant focus:outline-none focus:ring-2 focus:ring-primary/40"
      />
      {value ? (
        <button
          type="button"
          onClick={() => {
            setValue('');
            commitNow('');
          }}
          className="absolute right-2 top-1/2 -translate-y-1/2 rounded p-1 text-on-surface-variant transition-colors hover:text-on-surface"
          aria-label="Cancella ricerca"
        >
          <X size={14} strokeWidth={2.5} />
        </button>
      ) : null}
    </div>
  );
}
