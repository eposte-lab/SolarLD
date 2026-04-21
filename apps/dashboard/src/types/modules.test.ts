/**
 * Tests for the module-config type system and withModuleDefaults.
 *
 * These tests encode the invariant that originally caused the onboarding
 * step-2 crash: if withModuleDefaults returns an incomplete config, any
 * CheckboxGroup / RadioGroup calling `.includes()` on an undefined field
 * will throw "Cannot read properties of undefined (reading 'includes')".
 *
 * Regression guard: NEVER remove these tests without a migration path
 * that enforces the same invariant at the DB/API boundary.
 */
import { describe, it, expect } from 'vitest';

import {
  MODULE_KEYS,
  MODULE_LABELS,
  withModuleDefaults,
  DEFAULT_SORGENTE,
  DEFAULT_TECNICO,
  DEFAULT_ECONOMICO,
  DEFAULT_OUTREACH,
  DEFAULT_CRM,
} from './modules';
import type { ModuleKey, TecnicoConfig, SorgenteConfig } from './modules';

// ---------------------------------------------------------------------------
// 1. withModuleDefaults: complete config — no undefined fields
// ---------------------------------------------------------------------------

describe('withModuleDefaults — completeness', () => {
  it('sorgente: all array fields are initialised (never undefined)', () => {
    const cfg = withModuleDefaults('sorgente', null);
    // These are the exact fields that CheckboxGroup calls .includes() on.
    expect(Array.isArray(cfg.ateco_codes)).toBe(true);
    expect(Array.isArray(cfg.province)).toBe(true);
    expect(Array.isArray(cfg.regioni)).toBe(true);
    expect(Array.isArray(cfg.cap)).toBe(true);
  });

  it('tecnico: orientamenti_ok is an array (the crash culprit field)', () => {
    const cfg = withModuleDefaults('tecnico', null);
    // This is the exact field that crashed when sorgente config leaked into
    // the tecnico form: `value.orientamenti_ok.includes()` on undefined.
    expect(Array.isArray(cfg.orientamenti_ok)).toBe(true);
    expect(cfg.orientamenti_ok.length).toBeGreaterThan(0);
  });

  it('economico: all numeric fields are present', () => {
    const cfg = withModuleDefaults('economico', null);
    expect(typeof cfg.ticket_medio_eur).toBe('number');
    expect(typeof cfg.roi_target_years).toBe('number');
    expect(typeof cfg.budget_scan_eur).toBe('number');
    expect(typeof cfg.budget_outreach_eur_month).toBe('number');
  });

  it('outreach: channels object and all channel booleans present', () => {
    const cfg = withModuleDefaults('outreach', null);
    expect(typeof cfg.channels).toBe('object');
    expect(typeof cfg.channels.email).toBe('boolean');
    expect(typeof cfg.channels.postal).toBe('boolean');
    expect(typeof cfg.channels.whatsapp).toBe('boolean');
    expect(typeof cfg.channels.meta_ads).toBe('boolean');
  });

  it('crm: pipeline_labels is an array', () => {
    const cfg = withModuleDefaults('crm', null);
    expect(Array.isArray(cfg.pipeline_labels)).toBe(true);
    expect(cfg.pipeline_labels.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// 2. withModuleDefaults: cross-key isolation
// ---------------------------------------------------------------------------

describe('withModuleDefaults — cross-key isolation (regression: step-2 crash)', () => {
  it('sorgente config CANNOT satisfy tecnico shape (field mismatch)', () => {
    // If a sorgente config were fed into withModuleDefaults('tecnico'),
    // the merger should still produce a complete TecnicoConfig.
    // Root cause scenario: React reused ModulePanel across steps without
    // remounting, so state was sorgente-shaped when tecnico rendered.
    const sorgenteAsPartial = DEFAULT_SORGENTE as unknown as Partial<TecnicoConfig>;
    const cfg = withModuleDefaults('tecnico', sorgenteAsPartial);
    // orientamenti_ok must come from TecnicoConfig defaults, not sorgente
    expect(Array.isArray(cfg.orientamenti_ok)).toBe(true);
    // Sorgente has no orientamenti_ok — the default fills it in.
    expect(cfg.orientamenti_ok).toEqual(DEFAULT_TECNICO.orientamenti_ok);
  });

  it('tecnico config CANNOT pollute sorgente shape', () => {
    const tecnicoAsPartial = DEFAULT_TECNICO as unknown as Partial<SorgenteConfig>;
    const cfg = withModuleDefaults('sorgente', tecnicoAsPartial);
    // sorgente must have ateco_codes — filled from SorgenteConfig defaults
    expect(Array.isArray(cfg.ateco_codes)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 3. withModuleDefaults: partial merge respects provided values
// ---------------------------------------------------------------------------

describe('withModuleDefaults — partial merge', () => {
  it('provided scalar values win over defaults', () => {
    const cfg = withModuleDefaults('tecnico', { min_kwp: 99 });
    expect(cfg.min_kwp).toBe(99);
    // Other fields still come from defaults
    expect(cfg.orientamenti_ok).toEqual(DEFAULT_TECNICO.orientamenti_ok);
  });

  it('provided nested object merges with default nested object', () => {
    const cfg = withModuleDefaults('outreach', {
      channels: { email: false, postal: true, whatsapp: false, meta_ads: false },
    });
    expect(cfg.channels.email).toBe(false);
    expect(cfg.channels.postal).toBe(true);
    // Non-provided nested field falls back to default
    expect(typeof cfg.tone_of_voice).toBe('string');
  });

  it('null/undefined fields in partial are ignored (defaults fill in)', () => {
    const cfg = withModuleDefaults('economico', {
      ticket_medio_eur: undefined as unknown as number,
      roi_target_years: 8,
    });
    expect(cfg.ticket_medio_eur).toBe(DEFAULT_ECONOMICO.ticket_medio_eur);
    expect(cfg.roi_target_years).toBe(8);
  });
});

// ---------------------------------------------------------------------------
// 4. MODULE_KEYS registry completeness
// ---------------------------------------------------------------------------

describe('MODULE_KEYS registry', () => {
  const EXPECTED_KEYS: ModuleKey[] = ['sorgente', 'tecnico', 'economico', 'outreach', 'crm'];

  it('contains exactly the 5 expected module keys in order', () => {
    expect(MODULE_KEYS).toEqual(EXPECTED_KEYS);
  });

  it('every key has a non-empty MODULE_LABELS entry', () => {
    for (const k of MODULE_KEYS) {
      expect(typeof MODULE_LABELS[k]).toBe('string');
      expect(MODULE_LABELS[k].length).toBeGreaterThan(0);
    }
  });

  it('every key has a complete default config (no null/undefined top-level values)', () => {
    for (const k of MODULE_KEYS) {
      const cfg = withModuleDefaults(k, null);
      // All top-level values must be defined
      for (const [field, value] of Object.entries(cfg)) {
        expect(value, `${k}.${field} should not be undefined`).not.toBeUndefined();
      }
    }
  });
});

// ---------------------------------------------------------------------------
// 5. DEFAULT_OUTREACH channel flags sanity
// ---------------------------------------------------------------------------

describe('DEFAULT_OUTREACH channels', () => {
  it('email is enabled by default', () => {
    expect(DEFAULT_OUTREACH.channels.email).toBe(true);
  });

  it('whatsapp is disabled by default (requires business account setup)', () => {
    expect(DEFAULT_OUTREACH.channels.whatsapp).toBe(false);
  });

  it('postal is disabled by default (requires Pixart account)', () => {
    expect(DEFAULT_OUTREACH.channels.postal).toBe(false);
  });

  it('meta_ads is disabled by default (requires Meta OAuth)', () => {
    expect(DEFAULT_OUTREACH.channels.meta_ads).toBe(false);
  });

  it('withModuleDefaults preserves channel defaults when passed empty config', () => {
    const cfg = withModuleDefaults('outreach', {});
    expect(cfg.channels).toEqual(DEFAULT_OUTREACH.channels);
  });
});
