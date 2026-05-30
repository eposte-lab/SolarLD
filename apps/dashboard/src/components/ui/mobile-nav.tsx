'use client';

/**
 * MobileNav — shell di navigazione sotto `md`.
 *
 * La SideNav desktop è `hidden md:flex`, quindi sotto i 768px non
 * esisteva alcun modo di navigare. Questo componente aggiunge:
 *   - una top-bar sticky (`md:hidden`) con hamburger + brand lockup;
 *   - un drawer a scomparsa da sinistra che riusa lo STESSO `NavGroups`
 *     della SideNav (config nav in un solo posto) + footer tenant.
 *
 * Il drawer si chiude al cambio route (tap su un link), al tap sul
 * backdrop e sul bottone X. Blocca lo scroll del body mentre è aperto.
 */

import { LineChart, Menu, X } from 'lucide-react';
import { usePathname } from 'next/navigation';
import { useEffect, useState } from 'react';

import { NavGroups, type NavSection } from '@/components/ui/side-nav';
import { SignOutButton } from '@/components/ui/sign-out-button';

interface Props {
  sections: NavSection[];
  tenant: { business_name: string };
  user_email: string | null;
}

export function MobileNav({ sections, tenant, user_email }: Props) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  // Chiudi il drawer ad ogni cambio route (tap su un link nav).
  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  // Blocca lo scroll del body mentre il drawer è aperto.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = prev;
    };
  }, [open]);

  return (
    <>
      {/* Top-bar mobile — solo sotto md */}
      <div className="sticky top-0 z-30 flex items-center gap-3 ghost-border bg-surface-container-lowest/90 px-4 py-3 backdrop-blur-glass-sm md:hidden">
        <button
          type="button"
          onClick={() => setOpen(true)}
          aria-label="Apri menu"
          aria-expanded={open}
          className="flex h-9 w-9 items-center justify-center rounded-xl text-on-surface-variant transition-colors hover:bg-white/[0.06] hover:text-on-surface"
        >
          <Menu size={20} strokeWidth={2} aria-hidden />
        </button>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/total-trade-mark.png"
          alt="Total Trade"
          className="h-7 w-auto shrink-0"
        />
        <span className="font-headline text-[15px] font-bold tracking-tightest text-on-surface">
          Solar Trade Lead
        </span>
      </div>

      {/* Drawer: backdrop + pannello */}
      {open && (
        <div
          className="fixed inset-0 z-50 md:hidden"
          role="dialog"
          aria-modal="true"
          aria-label="Menu di navigazione"
        >
          <button
            type="button"
            aria-label="Chiudi menu"
            onClick={() => setOpen(false)}
            className="absolute inset-0 bg-black/50 backdrop-blur-[2px]"
          />
          <nav className="absolute left-0 top-0 flex h-full w-[280px] max-w-[85vw] flex-col bg-surface-container-lowest p-5 shadow-ambient">
            {/* Header drawer + chiudi */}
            <div className="mb-6 flex items-center gap-2.5">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src="/total-trade-mark.png"
                alt="Total Trade"
                className="h-9 w-auto shrink-0"
              />
              <div className="leading-tight">
                <h2 className="font-headline text-[16px] font-bold tracking-tightest text-on-surface">
                  Solar Trade Lead
                </h2>
                <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-on-surface-variant">
                  Installer Pro
                </p>
              </div>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Chiudi menu"
                className="ml-auto flex h-9 w-9 items-center justify-center rounded-xl text-on-surface-variant transition-colors hover:bg-white/[0.06] hover:text-on-surface"
              >
                <X size={20} strokeWidth={2} aria-hidden />
              </button>
            </div>

            <NavGroups
              sections={sections}
              className="-mx-1 flex-1 overflow-y-auto px-1"
            />

            {/* Footer tenant */}
            <div className="relative mt-5 overflow-hidden rounded-2xl liquid-glass-sm p-4">
              <span
                className="pointer-events-none absolute inset-0 bg-glass-specular"
                aria-hidden
              />
              <div className="relative mb-3 flex items-center gap-2.5">
                <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-primary">
                  <LineChart size={14} strokeWidth={2} aria-hidden />
                </div>
                <div className="min-w-0">
                  <p className="truncate text-[13px] font-semibold leading-tight text-on-surface">
                    {tenant.business_name}
                  </p>
                  {user_email && (
                    <p className="mt-0.5 truncate text-[11px] leading-tight text-on-surface-variant">
                      {user_email}
                    </p>
                  )}
                </div>
              </div>
              <div className="relative">
                <SignOutButton />
              </div>
            </div>
          </nav>
        </div>
      )}
    </>
  );
}
