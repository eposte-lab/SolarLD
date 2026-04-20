/**
 * Shared wizard form state — the shape the 5 steps read/write and
 * what `SubmitButton` serialises into a `POST /v1/tenant-config` body.
 *
 * Mirrors `WizardIn` on the API side (apps/api/src/routes/tenant_config.py).
 * Keep these two in sync: a field added here must be added there and
 * in `tenant_config_service.WizardPayload`.
 */

import type { ScanMode, TargetSegment } from '@/types/db';

export interface WizardForm {
  // Step 1
  scan_mode: ScanMode;
  target_segments: TargetSegment[];

  // Step 2
  ateco_codes: string[];

  // Step 3
  min_kwp_b2b: number | null;
  min_kwp_b2c: number | null;
  max_shading: number;
  min_exposure_score: number;

  // Step 4
  scan_priority_zones: string[];
  monthly_scan_budget_eur: number;
  monthly_outreach_budget_eur: number;

  // Step 5
  scoring_threshold: number;

  // Step 6 — provider integrations (all optional / skippable).
  // Persisted into `tenants.settings.*` by the API post-hook, NOT
  // into `tenant_configs`. Empty strings are filtered server-side so
  // skipping the step leaves existing values untouched.
  integrations: WizardIntegrations;
}

export interface WizardIntegrations {
  neverbounce_api_key: string;
  dialog360_token: string;
  dialog360_business_number: string;
  resend_webhook_secret: string;
}

export function defaultForm(): WizardForm {
  return {
    scan_mode: 'b2b_precision',
    target_segments: ['b2b'],
    ateco_codes: [],
    min_kwp_b2b: 50,
    min_kwp_b2c: null,
    max_shading: 0.4,
    min_exposure_score: 0.6,
    scan_priority_zones: ['capoluoghi'],
    monthly_scan_budget_eur: 1500,
    monthly_outreach_budget_eur: 2000,
    scoring_threshold: 60,
    integrations: {
      neverbounce_api_key: '',
      dialog360_token: '',
      dialog360_business_number: '',
      resend_webhook_secret: '',
    },
  };
}

/** Fixed priority-zone catalog — matches DB enum. */
export const PRIORITY_ZONES: { value: string; label: string; hint: string }[] = [
  {
    value: 'capoluoghi',
    label: 'Capoluoghi',
    hint: 'Milano, Torino, Napoli — alta densità',
  },
  {
    value: 'costa',
    label: 'Costa',
    hint: 'Insediamenti turistici e HORECA',
  },
  {
    value: 'zone_industriali',
    label: 'Zone industriali',
    hint: 'Capannoni + logistica',
  },
  {
    value: 'provincia',
    label: 'Provincia',
    hint: 'Comuni < 50k abitanti',
  },
];

/** Step 5 — scoring threshold buckets (UX sugar). */
export const THRESHOLD_BUCKETS: { value: number; label: string; desc: string }[] =
  [
    {
      value: 40,
      label: 'Aggressivo',
      desc: 'Quasi tutti i lead passano. Volume massimo, tasso di conversione basso.',
    },
    {
      value: 60,
      label: 'Equilibrato',
      desc: 'Default. Filtra i casi chiaramente deboli, manda il resto.',
    },
    {
      value: 75,
      label: 'Selettivo',
      desc: 'Solo lead forti. Meno outreach ma più conversioni.',
    },
    {
      value: 85,
      label: 'Elite',
      desc: 'Solo top-tier. Ideale per installatori con capacità limitata.',
    },
  ];

// ---------------------------------------------------------------------------
// Step validation — used by the shell to disable "Avanti".
// ---------------------------------------------------------------------------

export type StepId = 1 | 2 | 3 | 4 | 5 | 6;

export function canAdvance(step: StepId, f: WizardForm): boolean {
  switch (step) {
    case 1:
      return f.target_segments.length > 0;
    case 2:
      // b2b_precision needs at least one ATECO (Places whitelist).
      // Other modes can proceed with zero.
      return f.scan_mode !== 'b2b_precision' || f.ateco_codes.length > 0;
    case 3:
      return f.max_shading >= 0 && f.max_shading <= 1;
    case 4:
      return (
        f.scan_priority_zones.length > 0 &&
        f.monthly_scan_budget_eur >= 0 &&
        f.monthly_outreach_budget_eur >= 0
      );
    case 5:
      return f.scoring_threshold >= 0 && f.scoring_threshold <= 100;
    case 6:
      // Integrations are entirely optional — the user can skip the step
      // and drop in keys later from /settings. Always advanceable.
      return true;
  }
}
