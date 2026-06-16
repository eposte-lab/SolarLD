'use client';

/**
 * ResendToAddressButton — top-right action-bar entry point for the
 * "Reinvia a un altro indirizzo" flow. Opens a modal that hosts the existing
 * ResendToAddressForm, so the form no longer sits inline in the lead body —
 * it's behind the resend action where the operator expects it.
 */

import { Mail, X } from 'lucide-react';
import { useEffect, useState } from 'react';

import { ResendToAddressForm } from './ResendToAddressForm';

interface Props {
  leadId: string;
}

export function ResendToAddressButton({ leadId }: Props) {
  const [open, setOpen] = useState(false);

  // Close on Escape for keyboard users.
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-1.5 rounded-lg border border-on-surface/15 bg-surface-container-lowest px-3 py-1.5 text-xs font-semibold text-on-surface transition-colors hover:bg-surface-container"
      >
        <Mail size={13} strokeWidth={2.25} aria-hidden />
        Reinvia a un altro indirizzo
      </button>

      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Reinvia a un altro indirizzo"
          onClick={() => setOpen(false)}
          className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 pt-[10vh] backdrop-blur-sm"
        >
          <div onClick={(e) => e.stopPropagation()} className="relative w-full max-w-lg">
            <button
              type="button"
              onClick={() => setOpen(false)}
              aria-label="Chiudi"
              className="absolute -right-2 -top-2 z-10 inline-flex h-7 w-7 items-center justify-center rounded-full bg-surface-container-highest text-on-surface shadow-ambient transition-colors hover:bg-surface-container-high"
            >
              <X size={15} strokeWidth={2.5} aria-hidden />
            </button>
            <ResendToAddressForm leadId={leadId} />
          </div>
        </div>
      )}
    </>
  );
}
