# Sprint 4 — Creative Agent + ROI (Delivered)

**Duration**: Weeks 9-10
**Goal**: Turn each scored lead into visual assets (satellite "before" +
AI PV overlay "after") plus an indicative ROI block that the dashboard
and outreach templates can embed. Replicate drives the overlay; Mapbox
drives the satellite; Supabase Storage hosts the durable URLs; ROI is
pure math so it works even when Replicate is unavailable.

## 1. ROI calculator (`apps/api/src/services/roi_service.py`)

Pure dataclasses, no DB/HTTP. Italian-market calibrated 2026 Q2.

```
CAPEX_EUR_PER_KWP_B2C     = 1500
CAPEX_EUR_PER_KWP_B2B     = 1200
GRID_PRICE_EUR_PER_KWH_B2C = 0.25
GRID_PRICE_EUR_PER_KWH_B2B = 0.22
SELF_CONSUMPTION_RATIO_B2C = 0.40
SELF_CONSUMPTION_RATIO_B2B = 0.65
EXPORT_PRICE_EUR_PER_KWH   = 0.09
CO2_KG_PER_KWH             = 0.281
INCENTIVE_PCT_B2C          = 0.50   # Superbonus residenziale
INCENTIVE_PCT_B2B          = 0.30   # Credito d'imposta 4.0
INCENTIVE_PCT_FALLBACK     = 0.10   # unknown subjects, conservative
```

`compute_roi(estimated_kwp, estimated_yearly_kwh, subject_type)` returns
a frozen `RoiEstimate` with:

- `estimated_kwp`, `yearly_kwh` (derives the missing one from 1300 kWh/kWp)
- `gross_capex_eur`, `incentive_eur`, `net_capex_eur`
- `yearly_savings_eur` = `self_kwh × grid_price + export_kwh × RID`
- `payback_years` = `net_capex / yearly_savings` (None if savings=0)
- `co2_kg_per_year`, `co2_tonnes_25_years`
- `self_consumption_ratio`

`.to_jsonb()` projects the dataclass into the shape expected by the
`leads.roi_data` JSONB column (rounded and numeric only).

Refuses to invent numbers when inputs are too sparse: both kWp AND
yearly kWh missing → returns `None` and the caller skips the ROI block.

## 2. Replicate client (`apps/api/src/services/replicate_service.py`)

Async HTTP wrapper over `https://api.replicate.com/v1/predictions`:

```
DEFAULT_MODEL_VERSION = "7762fd07…9bdc"     # stability-ai/sdxl (pinned)
REPLICATE_COST_PER_CALL_CENTS = 1
```

Surface:

- `create_prediction(image_url, prompt, ...)` — POST /predictions, `@retry(3x, expo)`
- `fetch_prediction(id, ...)` — GET /predictions/{id}, `@retry(3x, expo)`
- `poll_prediction(id, poll=2s, max_wait=120s)` — blocking loop until
  succeeded/failed/canceled or `ReplicateTimeout`.
- `create_pv_rendering(before_image_url, prompt_ctx, ...)` — one-shot
  create + poll + return the final `PredictionResult`.
- `render_prompt(ctx)` — pure prompt builder from
  `RenderingPromptContext(area_sqm, exposure, brand_primary_color,
  subject_type)`.
- `parse_prediction(raw)` — pure JSON → `PredictionResult` (tested in
  isolation, no HTTP).

Calibration notes:

- `num_inference_steps = 40` / `guidance_scale = 7.5` /
  `prompt_strength = 0.55` (keeps ~45% of the original roof pixels so
  we don't hallucinate a new house).
- Negative prompt bans people, cars, text, watermarks.
- SDXL returns a list of output URLs; we always take `output[0]`.

## 3. CreativeAgent rewrite (`apps/api/src/agents/creative.py`)

End-to-end pipeline (before → after → ROI → lead update → event):

1. Load `leads`, then `roofs` + `subjects` + `tenants` (brand row).
2. **Idempotency**: if `lead.rendering_image_url` is already set and
   `force=False`, return immediately with `skipped=true` and the
   existing URL.
3. Compute ROI first — pure, independent of the image pipeline so
   `roi_data` still lands even if Replicate is unreachable.
4. Download the Mapbox static satellite tile (zoom 20, 768×768@2x)
   for the roof's lat/lng; upload it to
   `renderings/{tenant_id}/{lead_id}/before.png` and capture the
   public URL.
5. Call `create_pv_rendering(before_url, RenderingPromptContext)`;
   on success download the output and re-host at
   `renderings/{tenant_id}/{lead_id}/after.png` (durable — Replicate's
   `replicate.delivery` URLs expire in 24h).
6. `UPDATE leads SET roi_data, rendering_image_url`.
7. Transition `roofs.status` from `scored` → `rendered`.
8. Emit `lead.rendered` (or `lead.render_skipped` when after_url is
   unavailable) with the full output for the audit trail.

**Degraded paths** are all non-fatal:

- Missing `roof.lat/lng` → skip image path, still persist ROI.
- Mapbox 4xx/5xx → `skipped_reason="mapbox_unavailable"`, still
  persist ROI, `lead.render_skipped` event.
- Replicate failure / timeout → same pattern, `skipped_reason` records
  the concrete reason (`replicate_error`, `replicate_failed`,
  `replicate_canceled`).
- `api_usage_log` insert is best-effort (try/except) so billing
  bookkeeping never fails the agent.

Cost accounting: every Replicate call logs 1¢ to
`api_usage_log(provider='replicate', endpoint='predictions:create')`
with `{lead_id}` metadata for monthly rollup.

## 4. Routes (`apps/api/src/routes/leads.py`)

`POST /leads/:id/regenerate-rendering?force=true` is now wired end-to-end:

- 404s on unknown lead (tenant-scoped).
- Enqueues `creative_task` via arq with a deterministic job_id
  (`creative:{tenant_id}:{lead_id}`) so double-clicks collapse.
- `force=false` lets callers pass a dry-run that skips already-rendered
  leads; default `force=true` matches the dashboard "Regenerate" button
  behaviour (tenant explicitly asked for a new image).

## 5. Tests (`apps/api/tests/`)

**26 new pure-function tests** (total **142 passing**):

- `test_roi_calculations.py` (11) — B2C happy path, B2B rates, unknown
  fallback, None when inputs are empty, kWp↔yearly_kwh derivation,
  incentive percent by tier, payback zero-division, CO2 projections,
  `to_jsonb()` rounding, string coercion.
- `test_replicate_parser.py` (15) — first-output extraction, failed/
  canceled terminal states, processing not done, string output fallback,
  empty list, missing status default, non-string output defensive path,
  prompt determinism + key cues (aerial / photovoltaic / preserve outline /
  shadows), B2B vs B2C building-type hint, large-area industrial cue,
  S-exposure sun-azimuth cue, unknown subject generic fallback.

All 113 pre-existing Sprint 0→3 tests still pass (hunter grid/filters/
parser, identity helpers, claude vision parser, compliance hash, and
the full scoring subpackage).

## What's NOT in place (by design — Sprint 5)

- **Remotion transition video** (`rendering_video_url`) + GIF
  post-processing (`rendering_gif_url`). Sprint 5 owns the Node sidecar
  that consumes `before.png` + `after.png` + brand assets and produces
  the MP4/GIF pair. The DB columns already exist; the Creative Agent
  will populate them in a second pass or a dedicated post-processor.
- **Email / postal templates** consuming `rendering_image_url` +
  `roi_data` — this ships with the Outreach Agent in Sprint 6.
- **Brand logo overlay** on the after-image — we capture
  `tenant.brand_primary_color` into the prompt context but we don't
  yet composite the installer's `brand_logo_url`. Pillow-based overlay
  is trivial and lands when we introduce Pillow as a runtime dep in
  Sprint 5.

## Handoff checklist for Sprint 5

- [ ] Add `Pillow` to `apps/api/pyproject.toml` for the brand-logo
      composite in the Node sidecar interchange step.
- [ ] Stand up the Remotion sidecar (`apps/render/`) with a single
      template: before-pan, crossfade to after, outro with ROI text.
- [ ] Extend `CreativeAgent` with a second call that invokes the
      Remotion sidecar over HTTP once `after_url` is set, then fills
      `rendering_video_url` + `rendering_gif_url`.
- [ ] Manual smoke test: trigger `/leads/:id/regenerate-rendering` on
      a freshly scored lead, verify `before.png` + `after.png` appear in
      the `renderings` bucket and `leads.roi_data` is populated.
- [ ] Wire dashboard "Rigenera" button → POST regenerate-rendering and
      poll the lead for updated URLs.
