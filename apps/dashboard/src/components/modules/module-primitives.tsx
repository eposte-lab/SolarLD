'use client';

/**
 * Shared form atoms for the modular wizard (`components/modules/*`).
 *
 * Kept small and styleless-ish: each primitive only cares about the
 * data shape, not layout. Module forms compose them into section
 * cards with their own headings. We deliberately don't pull in a
 * component library (shadcn, Radix) for these — the dashboard stays
 * minimal-dep and tailwind-only.
 */

import { cn } from '@/lib/utils';

// ---------------------------------------------------------------------------
// Section card — wraps one logical group of fields inside a module form
// ---------------------------------------------------------------------------

export function FieldCard({
  title,
  hint,
  children,
  className,
}: {
  title: string;
  hint?: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        'rounded-2xl border border-outline-variant/30 bg-surface-container-low p-5 shadow-ambient-sm',
        className,
      )}
    >
      <h3 className="font-headline text-lg font-semibold tracking-tight text-on-surface">
        {title}
      </h3>
      {hint && (
        <p className="mt-1 text-sm text-on-surface-variant">{hint}</p>
      )}
      <div className="mt-4 space-y-3">{children}</div>
    </section>
  );
}

// ---------------------------------------------------------------------------
// NumberField — constrained numeric input with optional unit suffix
// ---------------------------------------------------------------------------

export function NumberField({
  label,
  value,
  onChange,
  min,
  max,
  step = 1,
  suffix,
}: {
  label: string;
  value: number | null;
  onChange: (v: number | null) => void;
  min?: number;
  max?: number;
  step?: number;
  suffix?: string;
}) {
  return (
    <label className="flex items-center justify-between gap-3 text-sm">
      <span className="text-on-surface">{label}</span>
      <span className="flex items-center gap-2">
        <input
          type="number"
          value={value ?? ''}
          min={min}
          max={max}
          step={step}
          onChange={(e) => {
            const v = e.target.value;
            onChange(v === '' ? null : Number(v));
          }}
          className="w-28 rounded-lg border border-outline-variant/40 bg-surface px-3 py-1.5 text-right tabular-nums focus:border-primary focus:outline-none"
        />
        {suffix && (
          <span className="w-10 text-xs text-on-surface-variant">{suffix}</span>
        )}
      </span>
    </label>
  );
}

// ---------------------------------------------------------------------------
// SliderField — percent or ratio inputs (0..1)
// ---------------------------------------------------------------------------

export function SliderField({
  label,
  value,
  onChange,
  min,
  max,
  step = 0.05,
  format = (v) => `${Math.round(v * 100)}%`,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step?: number;
  format?: (v: number) => string;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-sm">
        <span className="text-on-surface">{label}</span>
        <span className="font-mono tabular-nums text-primary">{format(value)}</span>
      </div>
      <input
        type="range"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-primary"
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Toggle — small boolean switch for channels / active flags
// ---------------------------------------------------------------------------

export function Toggle({
  label,
  value,
  onChange,
  hint,
}: {
  label: string;
  value: boolean;
  onChange: (v: boolean) => void;
  hint?: string;
}) {
  return (
    <label className="flex cursor-pointer items-start justify-between gap-3 rounded-lg border border-outline-variant/30 bg-surface px-3 py-2">
      <span>
        <span className="block text-sm font-medium text-on-surface">{label}</span>
        {hint && (
          <span className="mt-0.5 block text-xs text-on-surface-variant">
            {hint}
          </span>
        )}
      </span>
      <input
        type="checkbox"
        checked={value}
        onChange={(e) => onChange(e.target.checked)}
        className="mt-1 h-5 w-5 cursor-pointer accent-primary"
      />
    </label>
  );
}

// ---------------------------------------------------------------------------
// TagInput — comma-separated list bound to a string[]
// ---------------------------------------------------------------------------

export function TagInput({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
}) {
  // Defensive: tolerate a DB row that predates a schema addition and
  // has this field missing. Same guard in CheckboxGroup below.
  const safe = Array.isArray(value) ? value : [];
  return (
    <label className="block space-y-1">
      <span className="text-sm text-on-surface">{label}</span>
      <input
        type="text"
        value={safe.join(', ')}
        placeholder={placeholder}
        onChange={(e) => {
          const parts = e.target.value
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean);
          onChange(parts);
        }}
        className="w-full rounded-lg border border-outline-variant/40 bg-surface px-3 py-1.5 text-sm focus:border-primary focus:outline-none"
      />
      <span className="block text-[11px] text-on-surface-variant">
        Separa con virgole. Esempio: {placeholder ?? '10.51, 20.11'}
      </span>
    </label>
  );
}

// ---------------------------------------------------------------------------
// CheckboxGroup — multi-select for enum-like lists (orientamenti, labels)
// ---------------------------------------------------------------------------

export function CheckboxGroup<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly T[];
  value: T[];
  onChange: (v: T[]) => void;
}) {
  // Defensive: same reasoning as TagInput — if the DB row is missing
  // this field, treat it as an empty selection instead of crashing.
  const safe = Array.isArray(value) ? value : [];
  function toggle(opt: T) {
    const has = safe.includes(opt);
    onChange(has ? safe.filter((x) => x !== opt) : [...safe, opt]);
  }
  return (
    <div className="space-y-1">
      <span className="text-sm text-on-surface">{label}</span>
      <div className="flex flex-wrap gap-2">
        {options.map((o) => {
          const active = safe.includes(o);
          return (
            <button
              key={o}
              type="button"
              onClick={() => toggle(o)}
              className={cn(
                'rounded-full px-3 py-1 text-xs font-semibold transition-colors',
                active
                  ? 'bg-primary text-on-primary'
                  : 'bg-surface-container-high text-on-surface-variant hover:bg-surface-container-highest',
              )}
            >
              {o}
            </button>
          );
        })}
      </div>
    </div>
  );
}
