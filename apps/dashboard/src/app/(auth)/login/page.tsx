'use client';

import { useState } from 'react';
import { createBrowserClient } from '@/lib/supabase/client';
import { BrandLogo } from '@/components/ui/brand-logo';

export default function LoginPage() {
  const supabase = createBrowserClient();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    const { error } = await supabase.auth.signInWithPassword({ email, password });
    setLoading(false);
    if (error) {
      setError(error.message);
      return;
    }
    // Hard redirect — forces a full page reload so the middleware sees
    // the newly-set Supabase auth cookie in the next request headers.
    window.location.href = '/leads';
  }

  return (
    <main className="flex min-h-screen">
      {/* ── Left panel — hero photo (hidden on mobile) ───────────────── */}
      <div className="relative hidden w-[58%] flex-col lg:flex" aria-hidden>
        {/* Background image — drop the file at:
            apps/dashboard/public/demo/solar-warehouses.jpg
            (aerial of industrial warehouses with rooftop PV, golden hour) */}
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src="/demo/solar-warehouses.jpg"
          alt=""
          className="absolute inset-0 h-full w-full object-cover"
          loading="eager"
        />

        {/* Subtle dark overlay so the tagline reads cleanly */}
        <div className="absolute inset-0 bg-gradient-to-br from-black/60 via-black/30 to-transparent" />

        {/* Bottom-left tagline */}
        <div className="absolute bottom-10 left-10 max-w-sm">
          <p className="text-xs font-semibold uppercase tracking-widest text-white/60">
            SolarLead · Piattaforma B2B
          </p>
          <h1 className="mt-2 font-headline text-3xl font-extrabold leading-snug tracking-tight text-white">
            Identifica i tetti ad alto potenziale.<br />
            Converti i lead in contratti.
          </h1>
        </div>
      </div>

      {/* ── Right panel — login form ──────────────────────────────────── */}
      <div className="flex flex-1 flex-col items-center justify-center bg-surface px-6 py-12">
        <div className="w-full max-w-sm">
          {/* Brand mark */}
          <div className="mb-8 flex flex-col items-center text-center">
            <div className="mb-3">
              <BrandLogo size={56} title="SolarLead" className="rounded-full" />
            </div>
            <span className="font-headline text-4xl font-extrabold tracking-tighter text-primary">
              SolarLead
            </span>
            <p className="mt-1 text-sm text-on-surface-variant">
              Piattaforma per installatori fotovoltaici
            </p>
          </div>

          <form
            onSubmit={onSubmit}
            className="space-y-5 rounded-xl bg-surface-container-lowest p-8 shadow-ambient"
          >
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
                Accesso Installatore
              </p>
            </div>

            <div className="space-y-1">
              <label className="text-sm font-medium text-on-surface">Email</label>
              <input
                type="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="nome@azienda.it"
                className="w-full rounded-lg bg-surface-container-low px-3 py-2.5 text-sm text-on-surface placeholder:text-on-surface-variant/60 outline-none focus:ring-2 focus:ring-primary/40"
              />
            </div>

            <div className="space-y-1">
              <label className="text-sm font-medium text-on-surface">Password</label>
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="w-full rounded-lg bg-surface-container-low px-3 py-2.5 text-sm text-on-surface placeholder:text-on-surface-variant/60 outline-none focus:ring-2 focus:ring-primary/40"
              />
            </div>

            {error && (
              <p className="rounded-lg bg-error-container px-3 py-2 text-sm font-medium text-on-error-container">
                {error}
              </p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full rounded-full bg-gradient-primary px-4 py-3 text-sm font-bold text-on-primary shadow-ambient-sm transition-opacity hover:opacity-95 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {loading ? 'Accesso in corso…' : 'Accedi'}
            </button>
          </form>
        </div>
      </div>
    </main>
  );
}
