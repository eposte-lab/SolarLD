'use client';

/**
 * LeadFeedbackPicker — manual lead-status update.
 *
 * Until this PR there was no UI for setting `leads.feedback`, even
 * though the column gates the GSE practice creation flow ("Crea
 * pratica GSE" appears only when feedback === 'contract_signed').
 * That meant the only path to a GSE practice was direct SQL — the
 * operator literally couldn't do it from the dashboard.
 *
 * Compact dropdown rendered next to the other lead header chips.
 * Five options matching the InstallerFeedback enum on the API
 * (apps/api/src/models/enums.py:78). Sets contract_signed in one
 * click — the GSE link unlocks immediately on router.refresh.
 */

import { Check, ChevronDown } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useEffect, useRef, useState } from 'react';

import { api, ApiError } from '@/lib/api-client';

type Feedback =
  | 'qualified'
  | 'appointment_set'
  | 'not_interested'
  | 'not_reachable'
  | 'contract_signed'
  | 'wrong_data';

interface Option {
  value: Feedback;
  label: string;
  toneClass: string;
}

const OPTIONS: Option[] = [
  {
    value: 'contract_signed',
    label: 'Contratto firmato',
    toneClass: 'text-emerald-300',
  },
  { value: 'appointment_set', label: 'Appuntamento fissato', toneClass: 'text-emerald-300' },
  { value: 'qualified', label: 'Qualificato / interessato', toneClass: 'text-primary' },
  { value: 'not_reachable', label: 'Non raggiungibile', toneClass: 'text-amber-300' },
  { value: 'not_interested', label: 'Non interessato', toneClass: 'text-rose-300' },
  { value: 'wrong_data', label: 'Dati errati', toneClass: 'text-on-surface-variant' },
];

const LABEL_BY_VALUE: Record<Feedback, string> = OPTIONS.reduce(
  (acc, o) => {
    acc[o.value] = o.label;
    return acc;
  },
  {} as Record<Feedback, string>,
);

interface Props {
  leadId: string;
  currentFeedback: string | null;
}

export function LeadFeedbackPicker({ leadId, currentFeedback }: Props) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [submitting, setSubmitting] = useState<Feedback | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    function onClickOutside(e: MouseEvent) {
      if (!ref.current?.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, [open]);

  async function set(feedback: Feedback) {
    setSubmitting(feedback);
    setError(null);
    try {
      await api.patch(`/v1/leads/${leadId}/feedback`, { feedback });
      setOpen(false);
      router.refresh();
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : 'Errore nel salvare il feedback. Riprova.',
      );
    } finally {
      setSubmitting(null);
    }
  }

  const currentLabel =
    currentFeedback && LABEL_BY_VALUE[currentFeedback as Feedback]
      ? LABEL_BY_VALUE[currentFeedback as Feedback]
      : 'Imposta esito';

  return (
    <div ref={ref} className="relative inline-flex">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 rounded-full bg-surface-container-high px-3 py-1 text-xs font-semibold text-on-surface-variant transition-colors hover:bg-surface-container-highest hover:text-on-surface"
      >
        Esito: <span className="text-on-surface">{currentLabel}</span>
        <ChevronDown size={12} strokeWidth={2.25} aria-hidden />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-30 mt-1 w-64 overflow-hidden rounded-xl bg-surface-container-highest shadow-ambient ring-1 ring-on-surface/5">
          <ul className="py-1 text-sm">
            {OPTIONS.map((opt) => {
              const isCurrent = currentFeedback === opt.value;
              const isLoading = submitting === opt.value;
              return (
                <li key={opt.value}>
                  <button
                    type="button"
                    onClick={() => set(opt.value)}
                    disabled={submitting !== null}
                    className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left transition-colors hover:bg-surface-container-high disabled:opacity-60"
                  >
                    <span className={opt.toneClass}>{opt.label}</span>
                    {isLoading ? (
                      <span className="text-[10px] text-on-surface-variant">…</span>
                    ) : isCurrent ? (
                      <Check size={12} strokeWidth={2.5} className="text-primary" />
                    ) : null}
                  </button>
                </li>
              );
            })}
          </ul>
          {error && (
            <p className="border-t border-on-surface/10 px-3 py-2 text-[11px] text-rose-300">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
