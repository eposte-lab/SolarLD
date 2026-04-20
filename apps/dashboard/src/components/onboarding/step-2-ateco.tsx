'use client';

/**
 * Step 2 — ATECO multi-select.
 *
 * Server component parent (`/onboarding/page.tsx`) fetches the
 * catalog via `listAtecoOptions()` and passes it as a prop. We group
 * by `wizard_group` and render one accordion per group with a
 * multi-select checklist inside.
 *
 * A "Seleziona tutto / nessuno" bulk toggle per group is the main
 * UX affordance — installers tend to work by vertical (es. HORECA),
 * not by surgical pick.
 *
 * For non-b2b_precision modes the step is informational only: the
 * backend doesn't require codes, so we render a soft hint and keep
 * the selection optional.
 */

import { useMemo, useState } from 'react';

import { cn } from '@/lib/utils';
import type { AtecoOption } from '@/types/db';

import type { WizardForm } from './wizard-types';

// Human labels for wizard groups. DB stores the slug.
const GROUP_LABELS: Record<string, { title: string; emoji: string }> = {
  retail_gdo: { title: 'Retail & GDO', emoji: '🛒' },
  horeca: { title: 'HORECA', emoji: '🍝' },
  automotive: { title: 'Automotive', emoji: '🚗' },
  logistics: { title: 'Logistica & trasporti', emoji: '📦' },
  healthcare: { title: 'Sanità & cliniche', emoji: '🏥' },
  education: { title: 'Scuole & università', emoji: '🎓' },
  personal_services: { title: 'Servizi alla persona', emoji: '💇' },
  professional_offices: { title: 'Uffici professionali', emoji: '🏢' },
  industry_light: { title: 'Industria leggera', emoji: '🏭' },
};

export interface Step2Props {
  form: WizardForm;
  onChange: (f: WizardForm) => void;
  options: AtecoOption[];
}

export function Step2Ateco({ form, onChange, options }: Step2Props) {
  const groupsInOrder = useMemo(() => {
    const seen = new Set<string>();
    const order: string[] = [];
    for (const o of options) {
      if (!seen.has(o.wizard_group)) {
        seen.add(o.wizard_group);
        order.push(o.wizard_group);
      }
    }
    return order;
  }, [options]);

  const byGroup = useMemo(() => {
    const m: Record<string, AtecoOption[]> = {};
    for (const o of options) {
      (m[o.wizard_group] ??= []).push(o);
    }
    return m;
  }, [options]);

  // Track which groups are open. Default: none (compact overview).
  const [openGroups, setOpenGroups] = useState<Set<string>>(new Set());

  const selected = new Set(form.ateco_codes);

  function toggleCode(code: string) {
    const next = new Set(selected);
    if (next.has(code)) next.delete(code);
    else next.add(code);
    onChange({ ...form, ateco_codes: Array.from(next) });
  }

  function toggleGroup(group: string) {
    setOpenGroups((prev) => {
      const next = new Set(prev);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      return next;
    });
  }

  function bulkSet(group: string, value: boolean) {
    const groupCodes = (byGroup[group] ?? []).map((o) => o.ateco_code);
    const next = new Set(selected);
    for (const c of groupCodes) {
      if (value) next.add(c);
      else next.delete(c);
    }
    onChange({ ...form, ateco_codes: Array.from(next) });
  }

  const required = form.scan_mode === 'b2b_precision';

  return (
    <section className="space-y-6">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          Passo 2 di 5
        </p>
        <h2 className="mt-1 font-headline text-3xl font-bold tracking-tighter">
          Quali settori target?
        </h2>
        <p className="mt-2 max-w-xl text-sm text-on-surface-variant">
          {required ? (
            <>
              Con <strong>B2B Precision</strong> dobbiamo sapere dove cercare.
              Seleziona i settori dove vuoi che Hunter scansioni.
            </>
          ) : (
            <>
              Opzionale per questa modalità — la selezione sarà usata solo per
              arricchimento Atoka in fase di contratto.
            </>
          )}
        </p>
      </header>

      {/* Selection counter */}
      <div className="flex items-center justify-between rounded-xl bg-surface-container-lowest px-5 py-3 shadow-ambient-sm">
        <span className="text-sm text-on-surface-variant">
          Codici selezionati
        </span>
        <span className="font-headline text-2xl font-bold text-primary">
          {form.ateco_codes.length}
        </span>
      </div>

      {/* Group accordions */}
      <ul className="space-y-3">
        {groupsInOrder.map((group) => {
          const items = byGroup[group] ?? [];
          const groupCodes = items.map((o) => o.ateco_code);
          const groupSelected = groupCodes.filter((c) => selected.has(c)).length;
          const allSelected = groupSelected === groupCodes.length;
          const open = openGroups.has(group);
          const meta = GROUP_LABELS[group] ?? { title: group, emoji: '•' };

          return (
            <li
              key={group}
              className="overflow-hidden rounded-xl bg-surface-container-lowest shadow-ambient-sm"
            >
              <div className="flex items-center justify-between gap-3 p-5">
                <button
                  type="button"
                  onClick={() => toggleGroup(group)}
                  className="flex flex-1 items-center gap-3 text-left"
                >
                  <span className="text-2xl" aria-hidden>
                    {meta.emoji}
                  </span>
                  <div>
                    <p className="font-headline text-lg font-bold tracking-tight">
                      {meta.title}
                    </p>
                    <p className="text-xs text-on-surface-variant">
                      {items.length} codici ATECO ·{' '}
                      <span className="font-semibold text-primary">
                        {groupSelected} selezionati
                      </span>
                    </p>
                  </div>
                </button>
                <button
                  type="button"
                  onClick={() => bulkSet(group, !allSelected)}
                  className={cn(
                    'shrink-0 rounded-full px-4 py-1.5 text-xs font-semibold transition-colors',
                    allSelected
                      ? 'bg-surface-container-high text-on-surface-variant hover:bg-surface-container-highest'
                      : 'bg-primary text-on-primary hover:opacity-90',
                  )}
                >
                  {allSelected ? 'Deseleziona' : 'Tutti'}
                </button>
                <button
                  type="button"
                  onClick={() => toggleGroup(group)}
                  className="shrink-0 text-on-surface-variant"
                  aria-label={open ? 'Chiudi' : 'Apri'}
                >
                  <svg
                    viewBox="0 0 24 24"
                    className={cn(
                      'h-5 w-5 transition-transform',
                      open && 'rotate-180',
                    )}
                    fill="currentColor"
                  >
                    <path d="M7 10l5 5 5-5z" />
                  </svg>
                </button>
              </div>

              {open && (
                <ul className="space-y-1 bg-surface-container-low px-5 pb-5 pt-2">
                  {items.map((opt) => {
                    const checked = selected.has(opt.ateco_code);
                    return (
                      <li key={opt.ateco_code}>
                        <label
                          className={cn(
                            'flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2.5 transition-colors',
                            checked
                              ? 'bg-primary-container/50'
                              : 'hover:bg-surface-container',
                          )}
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggleCode(opt.ateco_code)}
                            className="h-4 w-4 shrink-0 accent-primary"
                          />
                          <span className="flex-1 text-sm">
                            <span className="font-semibold">
                              {opt.ateco_code}
                            </span>{' '}
                            <span className="text-on-surface-variant">
                              {opt.ateco_label}
                            </span>
                          </span>
                          <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                            {opt.target_segment}
                          </span>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          );
        })}
      </ul>

      {required && form.ateco_codes.length === 0 && (
        <p className="rounded-lg bg-secondary-container px-4 py-3 text-sm text-on-secondary-container">
          Seleziona almeno un codice ATECO per procedere.
        </p>
      )}
    </section>
  );
}
