# Sprint 2 — Vision Fallback + Identity Agent (Delivered)

**Duration**: Weeks 5-6
**Goal**: Fill the coverage holes of Google Solar with a Claude-Vision
fallback, and turn discovered roofs into enriched owner records via Visura
→ Atoka → Hunter.io → NeverBounce.

## What's in place

### Services (`apps/api/src/services/`)

- **`claude_vision_service.py`** — `estimate_roof_from_image(url, lat, lng)`:
  - Sends a Mapbox satellite tile URL to Claude Sonnet 4.5 via the
    Anthropic Messages API with an `image` block.
  - Conservative prompt that demands `has_building=true` only when Claude
    is ≥80% confident — false positives are the killer here.
  - `parse_vision_response()` + `projection_to_insight()` are pure
    helpers that (a) accept / reject the JSON response, (b) clamp
    out-of-range values, and (c) project it into a `RoofInsight`
    compatible with the Google Solar pipeline.
  - kWp derived conservatively as `area × 0.85 / 6 m²/kWp`; yearly kWh
    uses Italian yield × `max(0.4, shading_score)`.

- **`hunter_io_service.py`** — Hunter.io email finder:
  - `find_email(domain, first_name, last_name, company)` uses
    `/email-finder` (cheap per-email endpoint).
  - `domain_search(domain, seniority="executive,senior")` as broader
    fallback when we don't have a person name.
  - Returns typed `HunterEmailResult` with confidence score, sources
    count, and Hunter's internal `verified` flag (syntax+SMTP+non-catchall).

- **`neverbounce_service.py`** — single-email validity check:
  - Returns `VerificationResult` enum mapping → `.sendable` property
    (only VALID + CATCHALL are considered sendable).
  - Soft-fails upstream 5xx as UNKNOWN rather than raising — protects
    the pipeline while forcing the Outreach agent to skip that lead.

- **`italian_business_service.py`** — production HTTP wrappers for
  Visura (cadastral lookup by lat/lng) and Atoka (company profile by
  P.IVA). Both raise `EnrichmentUnavailable` when the API key is
  missing — the Identity agent catches this and degrades gracefully.

### Agents

- **`agents/hunter.py`** — Solar 404 now tries the Claude Vision
  fallback instead of skipping the point. Tracks `vision_calls` per
  point and bills them separately in `api_cost_cents`. Sets
  `data_source=mapbox_ai_fallback` on roofs that came through vision.

- **`agents/identity.py`** — Real pipeline replacing the Sprint 0 stub:
  1. Loads `roofs` row, checks existing `subjects` (idempotency).
  2. Visura cadastral lookup → intestatario (B2B or B2C).
  3. If P.IVA present → Atoka → Hunter.io email-finder → NeverBounce.
  4. Computes `pii_hash` (B2B: `business_name|vat`; B2C: `name|address`;
     fallback: `anon|cap|city`).
  5. Checks `global_blacklist(pii_hash)` and transitions
     `roof.status` → `identified` or `blacklisted`.
  6. Upserts `subjects(tenant_id, roof_id)` with the merged profile.
  7. Emits `subject.identified` event with full provenance.
  - **Degraded paths**: every provider is optional. Missing Visura key
    → classification inherited from roof.classification. Missing Atoka
    → minimal B2B row with just Visura fields. Missing Hunter.io →
    no email, Outreach routes postal. Missing NeverBounce → email
    stored but `decision_maker_email_verified=false`.
  - **Confidence score**: 0.35 (visura) + 0.25 (atoka) + 0.2 (hunter)
    + 0.1 (neverbounce) + 0.1 (verified flag), capped at 1.0.

### Configuration

- `.env` (gitignored) populated with real keys:
  - `GOOGLE_SOLAR_API_KEY`, `MAPBOX_ACCESS_TOKEN`, `REPLICATE_API_TOKEN`
  - `HUNTER_API_KEY`, `NEVERBOUNCE_API_KEY`, `RESEND_API_KEY`
  - `SUPABASE_SERVICE_ROLE_KEY` (confirmed via JWT decode)
- Still missing (pipeline degrades gracefully): `ANTHROPIC_API_KEY`,
  `VISURA_API_KEY`, `ATOKA_API_KEY`, `SUPABASE_JWT_SECRET`, DB password.

### Tests (`apps/api/tests/`)

22 new pure-function tests (total 48 passing locally):

- **`test_claude_vision_parser.py`** (10) — JSON parsing with code fences,
  `has_building=false` rejection, low-confidence rejection, malformed
  JSON, missing fields, value clamping, kWp projection, azimuth →
  exposure mapping, vision source marker on raw payload.
- **`test_identity_helpers.py`** (12) — confidence weights, pii_hash
  priority order (B2B vs B2C vs fallback), case/accent insensitivity,
  row builder merge precedence (Atoka legal_name wins over Visura),
  B2C excludes decision-maker fields, roof-address fallback when
  Visura fields missing.

## What's NOT in place (by design — Sprint 3)

- **Scoring Agent real algorithm**: technical + consumption + incentives
  + solvency + distance weighted score, leveraging `scoring_weights`
  table (V1 default already seeded) and `ateco_consumption_profiles`.
- **GSE regional incentives weekly scraper** feeding `regional_incentives`.
- **Dashboard lead scoring UI** (tier badges, sort by score, filter).

## Handoff checklist for Sprint 3

- [ ] Add `ANTHROPIC_API_KEY` to `.env` so Vision fallback actually runs
      when Google Solar 404s.
- [ ] Request Visura + Atoka API keys — without them, Identity returns
      low-confidence rows and Scoring has no financials to work with.
- [ ] Pull `SUPABASE_JWT_SECRET` from Supabase dashboard (Settings → API)
      so FastAPI can verify dashboard user tokens.
- [ ] Get the Supabase DB password for `SUPABASE_DB_URL` if you want
      direct `psql` access for seeding test data.
- [ ] First end-to-end smoke: `supabase db push` → insert test tenant
      + territory with real Napoli bbox → `POST /territories/:id/scan`
      → check `roofs` populates → hand-invoke `identity_task` per roof.
