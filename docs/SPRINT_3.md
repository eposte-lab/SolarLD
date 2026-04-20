# Sprint 3 — Scoring Agent + DB Live (Delivered)

**Duration**: Weeks 7-8
**Goal**: Replace the Scoring stub with the V1 PRD algorithm (technical +
consumption + incentives + solvency + distance, weights from
`scoring_weights`), bring the Supabase database online, and expose the
rescore API for the dashboard.

## 1. Database is live

All 12 migrations applied to the real Supabase project
`ppabjpryzzkksrbnledy` via the Supabase MCP tooling.

```
0001_extensions_and_enums        (uuid-ossp, pgcrypto, postgis, pg_trgm + 12 enums)
0002_tenants                     (+ tenant_members, set_updated_at trigger)
0003_territories                 (bbox JSONB + priority)
0004_roofs                       (UNIQUE(tenant_id, geohash), idx on status/class)
0005_subjects                    (B2B + B2C columns, pii_hash NOT NULL)
0006_leads                       (public_slug UNIQUE, score/score_tier)
0007_campaigns                   (channel + provider ids + scheduled_for)
0008_events                      (RANGE partitioned by occurred_at,
                                   ensure_events_partition() helper bootstraps
                                   default + current month + 2 future months)
0009_global_blacklist            (pii_hash UNIQUE, reason enum)
0010_auxiliary_tables            (ATECO profiles, regional_incentives,
                                   scoring_weights w/ V1 seed, email_warmup_status,
                                   api_usage_log)
0011_rls_policies                (auth_tenant_id() helper + 15 policies)
0012_storage_buckets             (renderings/public, postcards/private, branding/public)
```

Seeded:
- **25 ATECO profiles** covering manufacturing, retail, logistics, hospitality,
  professional services, education, healthcare.
- **scoring_weights v1** = `{"technical":25,"consumption":25,"incentives":15,"solvency":20,"distance":15}`
  (active=true).

`.env` now holds the anon key + new publishable key
(`sb_publishable_kkfOjQBPlbDGABLcaXnQ5A_hRoViL9q`). Still missing and
handled gracefully: `SUPABASE_JWT_SECRET`, DB password.

## 2. Services (`apps/api/src/services/scoring/`)

Brand-new subpackage — six pure modules, zero DB/HTTP dependencies,
each independently testable:

- **`technical.py`** — `technical_score(roof) -> int`.
  - kWp piecewise (2→30, 10→70, 15→95, 50+→100). Fallback to
    `area_sqm / 6 m²/kWp` when kWp unknown; returns 0 when both missing.
  - Pitch peaks at 20–40°, decays linearly outside the sweet spot.
  - Geometry base = `0.85·kwp_term + 0.15·pitch_term`, then multiplied
    by shading factor (0..1) and exposure factor (S=1.00, SE/SW=0.95,
    E/W=0.80, NE/NW=0.55, N=0.25).
  - `has_existing_pv=true` → hard 0 (they already went solar).

- **`consumption.py`** — `consumption_score(subject, roof, ateco) -> int`.
  - **B2B path**: base from `energy_intensity_tier` (high=75, med=50,
    low=30) + employee bonus (≥100 → +25, ≥20 → +15, ≥5 → +5) + ratio
    adjustment (projected consumption ÷ PV yield; ≥1.5 → +10, ≤0.3 → -15).
  - **B2B degradation**: no ATECO match → fall back on employees, then
    on roof-area proxy.
  - **B2C path**: derived from `area_sqm` (40→20, 80→40, 150→70,
    300→80, larger→85).

- **`incentives.py`** — `incentives_score(list, subject_type, today) -> int`.
  - Target filter: drop rows whose `target` doesn't match subject type.
  - Count curve: 0→20, 1→50, 2→75, 3+→90.
  - **Urgency bonus**: +10 (cap 100) if any applicable incentive has
    `deadline` within 90 days.
  - Malformed / past / missing deadlines are silently tolerated.

- **`solvency.py`** — `solvency_score(subject) -> int`.
  - B2B: revenue buckets (5M€→100, 1M→80, 200k→55, 50k→35, <50k→20).
    Revenue missing → fall back on employees (≥50→85, ≥10→65, ≥3→45, else 25).
  - B2C: 50 neutral (no SOI/ISEE integration yet).
  - UNKNOWN: 35.

- **`distance.py`** — `distance_score(roof_lat/lng, hq_lat/lng) -> int`.
  - Banded: ≤10km→100, ≤30→80, ≤60→60, ≤100→40, >100→20.
  - Either coord missing → 50 neutral (lets tenants score even before
    setting HQ coords in onboarding).

- **`combine.py`** — breakdown + weights + tier map.
  - `ScoringBreakdown` / `ScoringWeights` frozen dataclasses.
  - `combine_breakdown()` normalizes to weight sum (zero-sum edge case
    → plain average), clamps 0..100.
  - `tier_for()`: >75 HOT, 60–75 WARM, 40–59 COLD, <40 REJECTED.

- **`geo.py`** — province→region dict (all 107 ISTAT codes across 20
  regions) + haversine in km.

## 3. ScoringAgent (`apps/api/src/agents/scoring.py`)

Replaces the Sprint 0 stub. Pipeline:

1. Load `roofs` + `subjects` (tenant-scoped) + verify subject.roof_id
   matches — refuse cross-roof mismatches.
2. Load `tenants.settings.hq_lat/hq_lng` (optional).
3. Load active `scoring_weights` row (falls back to PRD default if
   somehow missing).
4. If B2B and `ateco_code` present → lookup
   `ateco_consumption_profiles[code]`.
5. Resolve subject province → region via `PROVINCE_TO_REGION`, then
   query active `regional_incentives` for that region.
6. Compute five subscores → `combine_breakdown` → `tier_for`.
7. **Upsert `leads`**: insert with a freshly-minted
   `secrets.token_urlsafe(16)` public_slug; if a lead already exists for
   `(tenant_id, roof_id, subject_id)` just update score fields (stable
   slug across rescores).
8. Transition `roofs.status` → `scored` iff it was still
   `discovered`/`identified`.
9. Emit `lead.scored` event with full breakdown + weights version for
   time-travel audit.

Degraded paths: missing HQ → distance=50, missing ATECO → consumption
falls back, missing incentives → score=20, all five subscores are
independent so one failure doesn't poison the rest.

## 4. Routes (`apps/api/src/routes/leads.py`)

Two new endpoints join the existing list/detail/timeline/feedback set:

- **`POST /leads/:id/rescore`** — enqueue a `scoring_task` for one lead
  (idempotent job_id: `scoring:<tenant>:<roof>:<subject>`).
- **`POST /leads/rescore-all?tier=cold&limit=500`** — bulk rescore with
  optional tier filter. Each enqueue is idempotent so double-clicks are
  safe. Returns `{queued, total_matching}`.

The existing `arq` worker (`workers/main.py::scoring_task`) already
dispatched to `ScoringAgent().run(...)` — no worker change needed.

## 5. Tests (`apps/api/tests/`)

65 new pure-function tests (total **113 passing**):

- `test_scoring_technical.py` (11) — kWp saturation, exposure N-penalty,
  shading decimation, pitch extremes, area fallback, existing-PV short-circuit,
  0..100 clamping.
- `test_scoring_consumption.py` (9) — B2B high-intensity, low-intensity,
  consumption-dwarfs-PV boost, oversized-array penalty, no-ATECO fallback,
  B2C villa vs apartment.
- `test_scoring_incentives.py` (11) — count curve, target filter (B2B vs
  B2C), urgency bonus, expired-deadline no-op, malformed dates.
- `test_scoring_solvency.py` (9) — revenue tiers, employee fallback,
  B2C neutral, UNKNOWN conservative, zero-revenue treated as missing.
- `test_scoring_distance.py` (13) — banded thresholds, neutral on
  missing coords, haversine sanity, province→region lookup (case,
  whitespace, unknown codes).
- `test_scoring_combine.py` (11) — JSONB weight loading, weighted
  average example (default weights), non-100 weight normalization,
  tier thresholds, zero-weight fallback to plain average, breakdown
  dict roundtrip, 0..100 clamping.

All 48 pre-existing Sprint 1+2 tests still pass (hunter grid/filters/parser,
identity helpers, claude vision parser, compliance hash).

## What's NOT in place (by design — Sprint 4)

- **GSE regional incentives weekly scraper** that populates
  `regional_incentives` — the scoring agent correctly handles an empty
  table (everyone gets incentives=20), and operators can manually insert
  rows in the meantime.
- **Creative Agent** (Mapbox static tile + Replicate PV overlay + ROI
  data → `leads.rendering_*` URLs).
- **Dashboard tier badges / score sort UI** — the REST endpoints return
  everything needed; frontend wiring ships in Sprint 4 alongside Creative.

## Handoff checklist for Sprint 4

- [ ] Set `tenants.settings = jsonb_build_object('hq_lat', ..., 'hq_lng', ...)`
      for each tenant during onboarding so distance scores stop defaulting
      to 50. Onboarding wizard (Sprint 11) will do this via Mapbox geocoder.
- [ ] Run `POST /territories/:id/scan` end-to-end, then `identity_task`
      on each discovered roof, then `scoring_task` on each subject → verify
      `leads` fills with sensible scores.
- [ ] Manually seed 2-3 `regional_incentives` rows (e.g. Campania
      "Bando Energia 2026" target=b2b, deadline=2026-06-30) so the
      incentives subscore exercises something other than the "nothing"
      baseline.
- [ ] Request the GSE incentives data source contact and add to the
      Sprint 4 scraper task list.
