/**
 * Settings → Devices.
 *
 * Admin surface for the device-authorization gate (migration 0074).
 * Lists every device that has ever held a session for this tenant —
 * active and revoked — with the metadata captured at first login:
 * UA-derived display name, IP /24 subnet, last seen, role.
 *
 * From here the operator can:
 *   • Revoke a device (frees its slot for the next login).
 *   • Promote a device to "admin" role (pins the operator's machine).
 *   • Demote admin → client.
 *   • Rename a device for clarity.
 *   • Toggle the gate on/off and tweak max_total + idle timeout.
 */

import Link from 'next/link';
import { redirect } from 'next/navigation';

import { BentoCard } from '@/components/ui/bento-card';
import { BadgeStatus } from '@/components/ui/badge-status';
import { SectionEyebrow } from '@/components/ui/section-eyebrow';
import { createSupabaseServerClient } from '@/lib/supabase/server';
import { getCurrentTenantContext } from '@/lib/data/tenant';
import { relativeTime } from '@/lib/utils';

import {
  demoteDeviceToClient,
  promoteDeviceToAdmin,
  renameDevice,
  revokeDevice,
  setDeviceGateEnabled,
} from './_actions';

export const dynamic = 'force-dynamic';

interface DeviceRow {
  id: string;
  display_name: string | null;
  user_agent: string | null;
  ip_subnet: string | null;
  role: 'admin' | 'client';
  authorized_at: string;
  last_seen_at: string;
  revoked_at: string | null;
}

interface TenantConfig {
  demo_device_limit_enabled: boolean;
  demo_device_max_total: number;
  demo_device_idle_timeout_minutes: number;
}

export default async function DevicesSettingsPage() {
  const ctx = await getCurrentTenantContext();
  if (!ctx) redirect('/login');

  const sb = await createSupabaseServerClient();

  // Fetch config + devices in parallel.
  const [tenantRes, devicesRes] = await Promise.all([
    sb
      .from('tenants')
      .select(
        'demo_device_limit_enabled, demo_device_max_total, demo_device_idle_timeout_minutes',
      )
      .eq('id', ctx.tenant.id)
      .maybeSingle(),
    sb
      .from('tenant_authorized_devices')
      .select(
        'id, display_name, user_agent, ip_subnet, role, authorized_at, last_seen_at, revoked_at',
      )
      .eq('tenant_id', ctx.tenant.id)
      .order('authorized_at', { ascending: false })
      .limit(50),
  ]);

  const cfg = (tenantRes.data ?? {
    demo_device_limit_enabled: false,
    demo_device_max_total: 3,
    demo_device_idle_timeout_minutes: 30,
  }) as TenantConfig;

  const devices = (devicesRes.data ?? []) as DeviceRow[];
  const active = devices.filter((d) => !d.revoked_at);
  const revoked = devices.filter((d) => d.revoked_at);
  const adminCount = active.filter((d) => d.role === 'admin').length;
  const clientCount = active.filter((d) => d.role === 'client').length;
  const slotsLeft = Math.max(0, cfg.demo_device_max_total - active.length);

  return (
    <div className="space-y-8">
      <header>
        <p className="text-[11px] font-semibold uppercase tracking-widest text-on-surface-variant">
          <Link href="/settings" className="hover:text-on-surface hover:underline">
            Impostazioni
          </Link>
          {' · '}Dispositivi
        </p>
        <h1 className="mt-1 font-headline text-4xl font-bold tracking-tighter text-on-surface md:text-5xl">
          Gate dispositivi
        </h1>
        <p className="mt-2 max-w-2xl text-sm text-on-surface-variant">
          Limita il numero di dispositivi fisici che possono accedere a
          questo account. Un device admin (la tua macchina) viene
          autorizzato manualmente, gli altri slot si riempiono al primo
          login dei client e restano fissi finché non revocati.
        </p>
      </header>

      {/* ── Master toggle + capacity ─────────────────────────────── */}
      <BentoCard span="full">
        <div className="space-y-4">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <SectionEyebrow>Configurazione</SectionEyebrow>
              <h2 className="mt-1 font-headline text-xl font-bold tracking-tighter">
                Gate {cfg.demo_device_limit_enabled ? 'attivo' : 'disattivato'}
              </h2>
              <p className="mt-1 text-xs text-on-surface-variant">
                Quando attivo, il middleware blocca il(({')'}{cfg.demo_device_max_total}+1)-esimo
                dispositivo con redirect a <span className="font-mono">/access-denied</span>.
              </p>
            </div>
            <BadgeStatus
              tone={cfg.demo_device_limit_enabled ? 'success' : 'neutral'}
              label={cfg.demo_device_limit_enabled ? 'Attivo' : 'Off'}
            />
          </div>

          <form action={setDeviceGateEnabled} className="space-y-3 rounded-xl bg-surface-container-lowest p-4">
            <div className="grid gap-4 md:grid-cols-3">
              <label className="space-y-1">
                <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                  Stato
                </span>
                <select
                  name="enabled"
                  defaultValue={cfg.demo_device_limit_enabled ? 'true' : 'false'}
                  className="w-full rounded-lg bg-surface-container px-3 py-2 text-sm"
                >
                  <option value="true">Attivo</option>
                  <option value="false">Disattivato</option>
                </select>
              </label>

              <label className="space-y-1">
                <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                  Slot totali
                </span>
                <input
                  type="number"
                  name="max_total"
                  min={1}
                  max={20}
                  defaultValue={cfg.demo_device_max_total}
                  className="w-full rounded-lg bg-surface-container px-3 py-2 text-sm tabular-nums"
                />
                <span className="block text-[11px] text-on-surface-variant">
                  Default 3 = 1 admin + 2 client.
                </span>
              </label>

              <label className="space-y-1">
                <span className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                  Idle timeout (min)
                </span>
                <input
                  type="number"
                  name="idle_minutes"
                  min={5}
                  max={1440}
                  defaultValue={cfg.demo_device_idle_timeout_minutes}
                  className="w-full rounded-lg bg-surface-container px-3 py-2 text-sm tabular-nums"
                />
                <span className="block text-[11px] text-on-surface-variant">
                  Auto-logout dopo inattività.
                </span>
              </label>
            </div>
            <div className="flex justify-end">
              <button
                type="submit"
                className="rounded-xl bg-primary px-5 py-2 text-sm font-semibold text-on-primary shadow-ambient-sm"
              >
                Salva configurazione
              </button>
            </div>
          </form>

          {/* Capacity strip */}
          <div className="grid grid-cols-3 gap-3 text-sm">
            <CapacityChip
              label="Slot occupati"
              value={`${active.length} / ${cfg.demo_device_max_total}`}
              tone={active.length >= cfg.demo_device_max_total ? 'warning' : 'neutral'}
            />
            <CapacityChip
              label="Admin"
              value={String(adminCount)}
              tone="primary"
            />
            <CapacityChip
              label="Slot liberi"
              value={String(slotsLeft)}
              tone={slotsLeft === 0 ? 'critical' : 'success'}
            />
          </div>
          <p className="text-xs text-on-surface-variant">
            Suggerimento: prima di attivare il gate per la prima volta,
            accedi dal tuo dispositivo, vai qui e clicca <em>Promuovi a admin</em>{' '}
            sul tuo device — così non rischi di occupare uno slot client.
          </p>
        </div>
      </BentoCard>

      {/* ── Active devices ───────────────────────────────────────── */}
      <BentoCard span="full" padding="tight">
        <header className="flex items-center justify-between px-2 pb-4 pt-2">
          <div>
            <SectionEyebrow>Dispositivi attivi</SectionEyebrow>
            <h2 className="font-headline text-2xl font-bold tracking-tighter">
              {active.length} {active.length === 1 ? 'dispositivo' : 'dispositivi'}
            </h2>
          </div>
          <span className="text-xs text-on-surface-variant">
            Admin {adminCount} · Client {clientCount}
          </span>
        </header>

        {active.length === 0 ? (
          <div className="rounded-lg bg-surface-container-low p-10 text-center text-sm text-on-surface-variant">
            Nessun dispositivo registrato. Al prossimo login il dispositivo
            verrà aggiunto automaticamente come <span className="font-mono">client</span>.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                  <th className="px-5 py-3">Nome</th>
                  <th className="px-5 py-3">Ruolo</th>
                  <th className="px-5 py-3">Subnet</th>
                  <th className="px-5 py-3">Autorizzato</th>
                  <th className="px-5 py-3">Ultimo accesso</th>
                  <th className="px-5 py-3 text-right">Azioni</th>
                </tr>
              </thead>
              <tbody className="bg-surface-container-lowest">
                {active.map((d, idx) => (
                  <tr
                    key={d.id}
                    style={
                      idx !== 0
                        ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }
                        : undefined
                    }
                  >
                    <td className="px-5 py-3">
                      <form action={renameDevice} className="flex items-center gap-2">
                        <input type="hidden" name="device_id" value={d.id} />
                        <input
                          name="display_name"
                          defaultValue={d.display_name ?? '—'}
                          className="w-48 rounded bg-surface-container px-2 py-1 text-xs text-on-surface focus:outline-none focus:ring-1 focus:ring-primary"
                        />
                        <button
                          type="submit"
                          className="text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant hover:text-on-surface"
                        >
                          Salva
                        </button>
                      </form>
                      {d.user_agent && (
                        <p className="mt-1 max-w-md truncate font-mono text-[10px] text-on-surface-variant/60">
                          {d.user_agent}
                        </p>
                      )}
                    </td>
                    <td className="px-5 py-3">
                      <BadgeStatus
                        tone={d.role === 'admin' ? 'success' : 'neutral'}
                        label={d.role === 'admin' ? 'Admin' : 'Client'}
                      />
                    </td>
                    <td className="px-5 py-3 font-mono text-xs text-on-surface-variant">
                      {d.ip_subnet ?? '—'}
                    </td>
                    <td className="px-5 py-3 text-xs text-on-surface-variant">
                      {relativeTime(d.authorized_at)}
                    </td>
                    <td className="px-5 py-3 text-xs text-on-surface-variant">
                      {relativeTime(d.last_seen_at)}
                    </td>
                    <td className="px-5 py-3">
                      <div className="flex flex-wrap justify-end gap-1.5">
                        {d.role === 'client' ? (
                          <form action={promoteDeviceToAdmin}>
                            <input type="hidden" name="device_id" value={d.id} />
                            <button
                              type="submit"
                              className="rounded-full ghost-border bg-surface-container px-2.5 py-1 text-[10px] font-semibold uppercase tracking-widest text-on-surface hover:bg-white/5"
                            >
                              Promuovi admin
                            </button>
                          </form>
                        ) : (
                          <form action={demoteDeviceToClient}>
                            <input type="hidden" name="device_id" value={d.id} />
                            <button
                              type="submit"
                              className="rounded-full ghost-border bg-surface-container px-2.5 py-1 text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant hover:bg-white/5"
                            >
                              Demoti
                            </button>
                          </form>
                        )}
                        <form action={revokeDevice}>
                          <input type="hidden" name="device_id" value={d.id} />
                          <button
                            type="submit"
                            className="rounded-full bg-error-container px-2.5 py-1 text-[10px] font-semibold uppercase tracking-widest text-on-error-container hover:opacity-90"
                          >
                            Revoca
                          </button>
                        </form>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </BentoCard>

      {/* ── History (revoked) ────────────────────────────────────── */}
      {revoked.length > 0 && (
        <BentoCard span="full" padding="tight">
          <header className="px-2 pb-4 pt-2">
            <SectionEyebrow>Storico revocati</SectionEyebrow>
            <h2 className="font-headline text-xl font-bold tracking-tighter">
              {revoked.length} dispositivi
            </h2>
          </header>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-[10px] font-semibold uppercase tracking-widest text-on-surface-variant">
                  <th className="px-5 py-3">Nome</th>
                  <th className="px-5 py-3">Subnet</th>
                  <th className="px-5 py-3">Revocato</th>
                </tr>
              </thead>
              <tbody className="bg-surface-container-lowest opacity-70">
                {revoked.map((d, idx) => (
                  <tr
                    key={d.id}
                    style={
                      idx !== 0
                        ? { boxShadow: 'inset 0 1px 0 rgba(170,174,173,0.15)' }
                        : undefined
                    }
                  >
                    <td className="px-5 py-3 text-on-surface-variant">
                      {d.display_name ?? '—'}
                    </td>
                    <td className="px-5 py-3 font-mono text-xs text-on-surface-variant/70">
                      {d.ip_subnet ?? '—'}
                    </td>
                    <td className="px-5 py-3 text-xs text-on-surface-variant">
                      {d.revoked_at ? relativeTime(d.revoked_at) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </BentoCard>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------

function CapacityChip({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: 'neutral' | 'primary' | 'success' | 'warning' | 'critical';
}) {
  const toneClass: Record<typeof tone, string> = {
    neutral: 'bg-surface-container-lowest text-on-surface',
    primary: 'bg-primary/10 text-primary',
    success: 'bg-tertiary-container text-on-tertiary-container',
    warning: 'bg-secondary-container text-on-secondary-container',
    critical: 'bg-error-container text-on-error-container',
  };
  return (
    <div className={`rounded-lg p-4 ${toneClass[tone]}`}>
      <p className="text-[10px] font-semibold uppercase tracking-widest opacity-70">
        {label}
      </p>
      <p className="mt-2 font-headline text-2xl font-bold tabular-nums tracking-tighter">
        {value}
      </p>
    </div>
  );
}
