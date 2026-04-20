# Sprint 5 — Remotion transition video + GIF (Delivered)

**Duration**: Weeks 11-12
**Goal**: Close the Creative pipeline by turning every `{before, after}`
image pair plus the `RoiEstimate` into a **6-second MP4 + lower-res GIF
outro**, branded with the installer's primary color and logo, and
uploaded to Supabase Storage. The dashboard and outreach templates now
have both a still image, an animated GIF (for email), and a vertical
social-ready MP4 (for WhatsApp / postal QR-landing pages).

Architecture: the render runs in a Node sidecar (`apps/video-renderer`)
driven by **Remotion 4 + React 19 + @remotion/renderer**. The FastAPI
Creative Agent POSTs to it over HTTP with the image URLs + ROI
numbers + brand info. Running Remotion out-of-process keeps ffmpeg /
Chromium out of the Python runtime and lets us scale the renderer
independently when volume ramps.

## 1. Remotion sidecar (`apps/video-renderer/`)

### `src/compositions/SolarTransition.tsx` — the scene

A single 180-frame composition at 30 fps (1080×1080, square — fits
Instagram/WhatsApp/postal QR landing layouts alike):

```
Frame  0–60   pure before shot           (2 s)
Frame 60–120  crossfade before → after   (2 s)
Frame 120–180 after + ROI outro + logo   (2 s)
```

Outro panel shows (Italian copy, localised for B2B/B2C alike):
- `{kwp} kWp` (big, in `brandPrimaryColor`)
- `€ {yearlySavingsEur} risparmio annuo`
- `Rientro stimato ~ {paybackYears} anni`
- `~ {co2TonnesLifetime} t CO₂ evitate in 25 anni` (optional)
- Small `Stima indicativa — preventivo formale a cura di {tenantName}`
- Brand logo anchored bottom-right with drop shadow

Zod schema `solarTransitionSchema`:
```
beforeImageUrl      URL
afterImageUrl       URL
kwp                 number
yearlySavingsEur    number
paybackYears        number
co2TonnesLifetime   number | undefined     (optional)
tenantName          string
brandPrimaryColor   hex (#rgb / #rrggbb / #rrggbbaa), default #0F766E
brandLogoUrl        URL | undefined        (optional)
```

### `src/render.ts` — the pipeline

Pure module (no Express) exporting `renderTransition(req, deps)`:

1. `getOrBuildBundle()` — `bundle({ entryPoint: remotion.tsx })` once
   per process; cached on the module. Bundling a Remotion project
   takes ~800ms cold; amortised this keeps every subsequent render at
   ~8-12s total.
2. `selectComposition` resolves fps/width/height from the Remotion
   entry so the server never has to hard-code them.
3. `renderMedia({ codec: 'h264', crf: 22 })` → `transition.mp4` in a
   unique tmp dir.
4. Same composition, halved resolution, `codec: 'gif'` →
   `transition.gif` with identical timing.
5. Both files are uploaded to Supabase Storage at
   `{bucket}/{outputPath}/transition.{mp4,gif}` with `upsert: true`
   (so reruns overwrite cleanly and the public URL is stable).
6. Returns `{ mp4Url, gifUrl, durationMs }`.

Security: `renderRequestSchema` adds `outputPath` (refused if absolute
or contains `..`) and `bucket` (default `"renderings"`). A malicious
caller cannot escape the tenant/lead folder.

### `src/server.ts` — Express bootstrap

Separates a pure `buildApp(deps)` factory from the process-starting
entry point so vitest can mount the app with a fake `render` and `supabase`:

```ts
POST /render   → 200 { mp4Url, gifUrl, durationMs }
               → 400 on zod validation failures (bad URLs, bad path)
               → 500 with the error.message on render/upload failure
GET  /health   → { status: 'ok', service: 'video-renderer', version }
```

Runs on `PORT=4000` by default.

## 2. Python client (`apps/api/src/services/remotion_service.py`)

Thin async HTTP wrapper:

```python
@dataclass(slots=True)
class RenderTransitionInput:
    before_image_url, after_image_url,
    kwp, yearly_savings_eur, payback_years,
    tenant_name, output_path,
    co2_tonnes_lifetime, brand_primary_color, brand_logo_url,
    bucket = "renderings"

async def render_transition(data, *, client=None, timeout_s=180.0)
    @retry(3x expo) on 5xx/timeouts
    raises RemotionError on 4xx (permanent)
```

Pure helpers (fully unit-tested):
- `build_render_request(dataclass)` → snake_case → camelCase JSON body
  that matches the zod schema exactly.
- `parse_render_response(raw)` — verifies both mp4Url *and* gifUrl are
  non-empty strings (no partial-success story), tolerates a missing /
  garbage `durationMs`.

`settings.video_renderer_url` default: `http://localhost:4000`.

## 3. CreativeAgent extension (`apps/api/src/agents/creative.py`)

New step 6 inserted between the Replicate upload and the DB update:

```
…
(before_url, after_url) = existing image pipeline
if before_url and after_url and roi is not None:
    try:
        transition = await render_transition(
            RenderTransitionInput(
                before_image_url=before_url,
                after_image_url=after_url,
                kwp=roi.estimated_kwp,
                yearly_savings_eur=roi.yearly_savings_eur,
                payback_years=roi.payback_years or 0.0,
                co2_tonnes_lifetime=roi.co2_tonnes_25_years,
                tenant_name=tenant.business_name,
                brand_primary_color=tenant.brand_primary_color,
                brand_logo_url=tenant.brand_logo_url,
                output_path=f"{tenant_id}/{lead_id}",
            )
        )
        video_url = transition.mp4_url
        gif_url = transition.gif_url
    except (RemotionError, httpx.HTTPError):
        log + set skipped_reason="remotion_error"
…
UPDATE leads SET
    rendering_image_url = <after_url>   (only when fresh)
    rendering_video_url = <video_url>   (only when fresh)
    rendering_gif_url   = <gif_url>     (only when fresh)
    roi_data            = <roi_jsonb>
```

**Degradation stays intact:** a sidecar outage never nukes a
previously-good video or image. We only overwrite the DB column when
we have a new value, so reruns converge.

`CreativeOutput` now carries `video_url` and `gif_url` alongside the
Sprint 4 `before_url` / `after_url`. The `lead.rendered` audit event
includes both so dashboards and tests can inspect the whole asset
bundle.

## 4. Tests

### Python — `apps/api/tests/` (12 new, **154 passing** total)

- `test_remotion_service.py` (12) — camelCase key enforcement, bucket
  + brand-color defaults, optional fields omitted when unset, numeric
  coercion, happy-path response, missing mp4Url → RemotionError,
  missing gifUrl → RemotionError, empty strings rejected, missing
  durationMs tolerated, garbage durationMs coerced to 0, non-string
  URL rejected.

All Sprint 0–4 tests still pass (hunter grid/filters/parser, identity
helpers, Claude vision parser, compliance hash, full scoring
subpackage, ROI calculator, Replicate parser).

### Node — `apps/video-renderer/src/__tests__/` (new)

- `schema.test.ts` — `solarTransitionSchema` + `renderRequestSchema`
  invariants: URL validation, hex-color validation (3/6/8 digits),
  absolute-path rejection, traversal rejection, `stripNonSchemaProps`
  removes `outputPath`+`bucket` before passing to Remotion, `joinPath`
  normalises segments.
- `server.test.ts` — uses `supertest` + an injected fake `render()`:
  - `GET /health` returns `{status:'ok', service:'video-renderer'}`
  - `POST /render` with a valid body returns the fake mp4/gif/duration
    and calls the render function exactly once with the parsed body
    (including the zod-filled `bucket` default).
  - `POST /render` with missing fields returns 400.
  - `POST /render` with a traversal `outputPath` returns 400.
  - `POST /render` where the render function throws returns 500 with
    the original error message.

Run with `pnpm --filter @solarlead/video-renderer test`.

## What's NOT in place (by design — Sprint 6)

- **Dashboard "Anteprima video"** — the MP4/GIF URLs flow through
  `GET /leads/:id` already; the frontend card will render an HTML5
  `<video>` when `rendering_video_url` is present (ships alongside the
  Outreach UI in Sprint 6).
- **Second-pass sidecar retry** from a scheduled cron — if a batch
  render fails because the sidecar is down, we rely on the tenant
  pressing "Rigenera" for now. A nightly `creative_retry` job that
  finds `leads WHERE rendering_video_url IS NULL AND rendering_image_url IS NOT NULL`
  is a 10-line cron task we can add in Sprint 6.
- **Adaptive duration** — currently every transition is 6s. Longer
  outros for high-value HOT leads (8s with price anchor) is easy but
  not worth the calibration effort until we have real conversion data.

## Handoff checklist for Sprint 6 (Outreach)

- [ ] Deploy the sidecar to Fly.io (`fly launch` inside
      `apps/video-renderer`); set `SUPABASE_URL` +
      `SUPABASE_SERVICE_ROLE_KEY`; update `settings.video_renderer_url`
      to the Fly hostname.
- [ ] Smoke test: pick one HOT lead, call
      `POST /leads/:id/regenerate-rendering?force=true`, verify both
      `renderings/{tenant}/{lead}/transition.mp4` and `.gif` appear in
      the bucket and that `leads.rendering_video_url` is set.
- [ ] Wire the email template (`packages/templates/email/preview.mjml`)
      to embed the GIF inline and link the MP4 for subscribers on
      modern clients.
- [ ] Add `video_url` + `gif_url` to the OpenAPI schema for
      `GET /leads/:id` so the Next.js dashboard type-checks against
      the real shape.
