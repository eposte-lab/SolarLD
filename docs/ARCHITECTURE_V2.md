# SolarLead v2 — Architecture

Snapshot of the platform after the April 2026 refactor. Supersedes the
B2B single-stage scan + monolithic wizard + shared B2C/B2B pipeline
described in `ARCHITECTURE.md`. Read that first for the orientation
(agents, arq worker, Supabase surfaces); this doc describes only the
things v2 changed.

TL;DR — three overlapping reworks:

1. **B2B Funnel 4-level** replaces a one-shot Google Places scan. Solar
   is now called on the top 20% of candidates scored by Claude Haiku
   rather than every candidate. Lead-per-euro quality climbs 5–10×.
2. **Modular wizard** replaces the fixed 6-step onboarding. Five
   independent modules (Sorgente / Tecnico / Economico / Outreach / CRM)
   persist in `tenant_modules`; installers reconfigure per-module after
   onboarding via `/settings/modules/<key>`.
3. **B2C Residential** gets a dedicated pipeline. CAP-level income
   dataset (ISTAT) → audience segments → outreach via letters (Pixart),
   Meta Lead Ads, or door-to-door dossier export. Solar runs *after*
   engagement, not before — inverts the funnel.

---

## 1. B2B Funnel 4-level

```
L1 Discovery       L2 Enrichment       L3 Proxy score     L4 Solar gate
(Atoka)            (Places + Web)      (Claude Haiku)     (top 20%)
──────────         ────────────        ─────────────      ────────────
 ~500-5000    →       same set    →       same set    →     ~100 leads
 €0.01/rec          €0.02/rec          €0.001/rec         €0.03/rec
                                                         only on top-N
```

### Why it matters commercially
- Old flow: `b2b_precision` called Solar on every Places candidate
  (~5000/scan × €0.03 = €150/scan). Margin on a €1.5k/month installer
  was ~40%.
- New flow: Solar only on top 20% after a proxy scoring pass
  (~€15/scan). Margin climbs to ~75%.

### Code map
- `apps/api/src/agents/hunter_funnel/level1_discovery.py` — Atoka
  `atoka_search_by_criteria` paginated query using the tenant's
  Sorgente module (ATECO, employees range, revenue range, geo).
  Persists raw hits into `scan_candidates` (stage=1).
- `level2_enrichment.py` — Places Text Search → Place Details to add
  phone/website/rating, plus an optional website fetch for
  "capannone/stabilimento" indicators.
- `level3_proxy_score.py` — Claude Haiku batch scoring. Prompt lives
  at `apps/api/src/prompts/proxy_score.md`. Batched 10 candidates per
  request to amortise prompt cache.
- `level4_solar_gate.py` — sort by `score DESC`, take top N where
  `N = max(20, total * tecnico.solar_gate_pct)`, call Solar,
  upsert roofs + subjects + leads for survivors.
- `apps/api/src/services/scan_cost_tracker.py` — per-scan rollup of
  Atoka / Places / Claude / Solar spend → exposed as
  `/v1/scans/{id}/costs`.

Each level emits `scan.l{n}.complete` events so the dashboard shows a
real-time waterfall (500 → 500 → 500 → 95 → 30).

### Migration path
- As of migration 0035 (April 2026) the legacy modes
  `b2b_precision`, `b2b_ateco_precision`, `opportunistic`, and
  `volume` are **gone**. The `tenant_configs` and `ateco_google_types`
  tables were dropped. `ScanMode` is now exactly
  `Literal["b2b_funnel_v2", "b2c_residential"]` — enforced in Python
  and in the database (scans dispatch raises on unknown modes).
- Default for Precision tenants: `b2b_funnel_v2`. Default for
  Residential tenants: `b2c_residential`. The scan mode lives on the
  Sorgente module (`SorgenteConfig.mode`), not on a separate config
  table.

---

## 2. Modular wizard

Five modules, each a row in `tenant_modules (tenant_id, module_key,
config JSONB, active BOOL, version INT)`:

| Module     | What it holds                               | Feeds                                 |
|------------|---------------------------------------------|---------------------------------------|
| Sorgente   | ATECO codes, employee/revenue range, geo    | L1 Atoka query                        |
| Tecnico    | Min kWp, min area, orientations, solar_gate_pct | L3 prompt + L4 filter            |
| Economico  | Ticket medio, ROI target, budget scan       | L2/L4 sampling cap                    |
| Outreach   | Channels (email/postal/WA/meta), tone, CTA  | CreativeAgent, OutreachAgent          |
| CRM        | Webhook URL, HMAC, pipeline labels, SLA     | CRMAgent                              |

Pydantic v2 schemas per module in `tenant_module_service.py` with
`ConfigDict(extra="forbid")` — mirrored 1:1 in
`apps/dashboard/src/types/modules.ts` for end-to-end type safety.

Onboarding flow at `/onboarding` runs the 5 module forms in sequence
(each skippable). Post-onboarding reconfiguration lives at
`/settings/modules/<key>`. There is no legacy wizard — the v1
six-step flow was deleted alongside `tenant_configs` in migration
0035.

`tenant_config_service.get_for_tenant()` is now a thin projection
layer: it reads `tenant_modules` and assembles a frozen `TenantConfig`
dataclass for the rest of the runtime. Writes go exclusively through
`tenant_module_service.upsert_module`.

---

## 3. B2C Residential

### Data source — ISTAT income per CAP
- `geo_income_stats (cap PK, provincia, regione, comune, reddito_medio_eur,
  popolazione, case_unifamiliari_pct)` populated by
  `scripts/load_istat_income.py` from the published MEF "redditi"
  dataset + census single-family-house share.
- One-off load, not per-tenant — every tenant queries the same table.

### Scan mode `b2c_residential`
- No Atoka, Places, or Solar calls at scan time. Costs ~€0 per scan.
- Pipeline (in `agents/hunter_b2c.py::run_b2c_residential`):
  1. Read Sorgente module's `reddito_min_eur` + `case_unifamiliari_pct_min`.
  2. Filter `geo_income_stats` by territory + those thresholds.
  3. Upsert one `b2c_audiences` row per CAP with bucket, household
     estimate, active channels snapshot.
  4. Emit `scan.b2c_audiences_ready`.
  5. Return `HunterOutput(places_found=audience_count, roofs_discovered=0)`.

### Channels

| Channel          | Endpoint                                                | Provider     |
|------------------|--------------------------------------------------------|--------------|
| Letter           | `POST /v1/b2c/audiences/{id}/mail-campaign`            | Pixart       |
| Meta Lead Ads    | `POST /v1/b2c/audiences/{id}/meta-campaign`            | Meta Graph   |
| Door-to-door PDF | `GET  /v1/b2c/audiences/{id}/export.pdf`               | reportlab    |
| Door-to-door xlsx| `GET  /v1/b2c/audiences/{id}/export.xlsx`              | openpyxl     |

All three are gated on the audience's `canali_attivi` snapshot.
Letter + Meta services currently run in *stub mode* — they return a
synthetic id when external creds aren't configured, so dashboard flows
are exercisable before Meta app review and Pixart contract are live.

### Inverted funnel — Solar after engagement

B2C leads are captured **without** a roof. `migrations/0034` loosens
`leads.roof_id` / `subject_id` to nullable for B2C sources
(`b2c_meta_ads`, `b2c_post_engagement`) — a CHECK constraint keeps
B2B rows NOT-NULL as before.

When a B2C lead signals positive intent (Meta form submit, email
reply with `sentiment='positive'`, etc.):

```
RepliesAgent detects positive sentiment
  └─ enqueues b2c_post_engagement_qualify_task
       └─ services/b2c_qualify_service.py::qualify_b2c_lead
            1. Extract address from inbound_payload
            2. forward_geocode → lat/lng
            3. Solar buildingInsights
            4. Upsert roof + b2c subject (pii_hash = sha256(name|cap))
            5. Attach roof_id + subject_id to the lead
            6. Flip source → 'b2c_post_engagement', status → 'qualified'
```

This aligns Solar API spend with commercial outcomes — we only pay
for roof qualification on leads that self-identified.

### Meta Lead Ads webhook

`POST /v1/webhooks/meta-leads`:
- HMAC-SHA256 validation against `meta_connections.webhook_secret`
  (per-tenant, keyed on Meta page id).
- Upsert lead with `source='b2c_meta_ads'`, `meta_lead_id` unique
  partial index per tenant for idempotency.
- Enqueue `meta_lead_enrich_task` to hit Graph API
  `/{leadgen_id}?fields=field_data` out-of-band. Today a stub
  (pending Meta app review); will land for real in a follow-up.

`GET /v1/webhooks/meta-leads` handles Meta's one-time subscription
challenge (`hub.mode=subscribe` + `hub.verify_token`).

---

## Migrations added in v2

| #    | Adds                                                                 |
|------|----------------------------------------------------------------------|
| 0030 | Scan mode `b2b_funnel_v2` (rename + CHECK update)                    |
| 0031 | `scan_candidates` table (L1/L2/L3 staging)                           |
| 0032 | `tenant_modules` (5 module rows per tenant, JSONB config)            |
| 0033 | `geo_income_stats` (ISTAT dataset) + `b2c_audiences`                 |
| 0034 | `meta_connections` + `leads.source`/`meta_lead_id`/`inbound_payload`; relax `leads.roof_id`/`subject_id` NOT NULL for B2C sources |

---

## Operational notes

- **Secrets validator** (`apps/api/src/core/config.py`) refuses staging
  startup if Meta is half-configured (`META_APP_ID` set but secret or
  verify-token missing).
- **Cost tracker** (`scan_cost_tracker.py`) is the single place to
  verify that the v2 funnel actually saves money — check
  `/v1/scans/{id}/costs` after every scan in staging.
- **Idempotency**: the Meta webhook upserts on
  `(tenant_id, meta_lead_id)` so retries collapse. The B2C qualify
  service short-circuits if the lead already has a roof. The scan
  pipeline is idempotent on `(tenant_id, scan_id, cap)` for audiences
  and on `(tenant_id, geohash)` for roofs.
- **GDPR**: door-to-door exports contain only CAP-level stats + blank
  fields; no PII leaves via that route. Meta form fields are encrypted
  at rest (Supabase column-level crypto planned; today guarded by RLS).

---

## File tree — what's new in v2

```
apps/api/src/
  agents/hunter_funnel/          # NEW — 4-level funnel modules
    level1_discovery.py
    level2_enrichment.py
    level3_proxy_score.py
    level4_solar_gate.py
  agents/hunter_b2c.py           # NEW — b2c_residential pipeline
  services/
    b2c_audience_service.py      # NEW
    b2c_qualify_service.py       # NEW — inverted funnel Solar
    meta_ads_service.py          # NEW — Meta Marketing API wrapper
    pixart_service.py            # NEW — letter campaigns
    scan_cost_tracker.py         # NEW — per-scan cost rollup
    tenant_module_service.py     # NEW — 5-module Pydantic + DAO
  routes/
    b2c_outreach.py              # NEW
    b2c_exports.py               # NEW — PDF + xlsx
    modules.py                   # NEW
  prompts/proxy_score.md         # NEW — Claude Haiku L3 prompt
  scripts/
    load_istat_income.py         # NEW — ISTAT loader
    migrate_wizard_to_modules.py # NEW — backfill

apps/dashboard/src/
  app/(onboarding)/onboarding/modular/page.tsx        # NEW
  app/(dashboard)/settings/modules/[key]/page.tsx     # NEW
  components/modules/                                 # NEW — 5 forms
  types/modules.ts                                    # NEW — TS mirror

packages/db/migrations/
  0030_scan_mode_funnel_v2.sql
  0031_scan_candidates_l1.sql
  0032_tenant_modules.sql
  0033_geo_income_stats.sql
  0034_meta_integration.sql
```
