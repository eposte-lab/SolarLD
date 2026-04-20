# Sprint 6 — Outreach Agent + Resend email + templates + tracking (Delivered)

**Duration**: Weeks 13-14
**Goal**: Send the first transactional email. A tenant can now press
"send outreach" on any Hot/Warm B2B lead and the platform will:

1. Check compliance (cross-tenant blacklist, opt-outs, verified email).
2. Render a branded Jinja2 + premailer HTML/text email using the
   lead's rendering, GIF, ROI numbers, and brand colour.
3. Optionally ask Claude for a one-sentence personalised opener.
4. Deliver via **Resend** (`POST /emails`).
5. Persist a `campaigns` row, move the lead to `pipeline_status='sent'`.
6. Receive **Svix-signed** webhooks back (`delivered` / `opened` /
   `clicked` / `bounced` / `complained`) and progress the lead
   pipeline accordingly — bounces and complaints auto-blacklist via
   the existing ComplianceAgent.

All pieces are idempotent and fail non-destructively: a Resend outage
leaves a `campaigns.status='failed'` row instead of a partial send;
webhook retries collapse on the Svix message id.

---

## 1. Resend HTTP client (`src/services/resend_service.py`)

Thin async client, intentionally tiny API surface:

```python
class ResendError(Exception): ...
class ResendSignatureError(Exception): ...

@dataclass(slots=True)
class SendEmailInput:
    from_address: str
    to: list[str]
    subject: str
    html: str
    text: str | None = None
    reply_to: str | None = None
    tags: dict[str, str] | None = None        # → [{"name":k,"value":v}] in Resend
    headers: dict[str, str] | None = None

@dataclass(slots=True)
class SendEmailResult:
    id: str   # Resend message id

@dataclass(slots=True)
class EmailEvent:
    id: str                         # Svix msg id — idempotency key
    type: str                       # delivered|opened|clicked|bounced|complained|…
    email_id: str                   # Resend's msg id → matches campaigns.email_message_id
    occurred_at: str | None
    to: list[str]
    raw: dict
```

### Pure helpers (all unit-tested)

- `build_send_payload(SendEmailInput) -> dict` — serialise to Resend's
  JSON shape. Tags become `[{"name": k, "value": v}]`; optionals are
  omitted (no `null`-leaking keys).
- `parse_send_response(raw) -> SendEmailResult` — extracts `id`,
  raises `ResendError` on empty/missing.
- `parse_webhook_event(raw) -> EmailEvent` — normalises the envelope
  (`email.delivered` → `delivered`; `data.email_id` **or** `data.id`;
  `to` as string **or** list).
- `verify_webhook_signature(body, svix_id, svix_timestamp,
  svix_signature, secret, tolerance_seconds=300, now_ts=None) -> bool`
  — Svix-style HMAC-SHA256:
  1. Strip `whsec_` prefix, base64-decode key.
  2. Sign `f"{msg_id}.{ts}.".encode() + body`.
  3. Accept the request if **any** of the space-separated
     `v1,<base64>` candidates match via `hmac.compare_digest`.
  4. Reject stale timestamps (> 5 min drift) to defeat replay.
  5. Tolerates rotated keys (multiple `v1,…` candidates in header).

### HTTP entry point

```python
@retry(stop=stop_after_attempt(3),
       wait=wait_exponential(multiplier=1, min=1, max=8),
       reraise=True)
async def send_email(data: SendEmailInput, *, client=None, timeout_s=30.0)
    -> SendEmailResult
```

4xx = permanent (bad from-domain, suppressed address), 5xx = retried.

---

## 2. Email templates (`packages/templates/email/`)

Shared package so the lead-portal (Next.js) can reuse the same markup
for preview. File layout:

```
_base.html.j2              shared scaffold — header, hero, ROI box, CTA, footer
outreach_b2b.html.j2       business-toned body (extends _base)
outreach_b2c.html.j2       residential-toned body (extends _base)
outreach_b2b.txt.j2        plain-text twin for multipart
outreach_b2c.txt.j2        plain-text twin for multipart
```

Design: max-width 600px card, 4px brand-colour top bar, hero
`rendering_gif_url` (falls back to `rendering_image_url`), Italian
copy, ROI block ("Potenza installabile", "Risparmio annuo stimato",
"Rientro stimato", "CO₂ evitata (25 anni)"), CTA button linking to
`lead_url`, signed "Il team di {{ tenant_name }}", footer with one-
click opt-out.

### `src/services/email_template_service.py` — renderer

```python
@dataclass(slots=True, frozen=True)
class OutreachContext:
    tenant_name: str
    brand_primary_color: str
    greeting_name: str
    lead_url: str
    optout_url: str
    subject_template: str
    subject_type: str           # b2b|b2c|unknown
    roi: dict | None
    hero_image_url: str | None
    hero_gif_url: str | None
    personalized_opener: str | None
    business_name: str | None
    ateco_code: str | None
    ateco_description: str | None

@dataclass(slots=True, frozen=True)
class RenderedEmail:
    subject: str
    html: str
    text: str

def render_outreach_email(ctx: OutreachContext) -> RenderedEmail: ...
def default_subject_for(subject_type: str, tenant_name: str) -> str: ...
```

Pipeline: Jinja2 (`StrictUndefined`, `autoescape` on html/htm/xml/j2)
renders both `.html.j2` + `.txt.j2` → `premailer.transform(html,
keep_style_tags=True, remove_classes=False)` inlines every CSS rule
onto the matching tag (Gmail preview-mode strips `<style>`, so we
can't rely on it alone).

Custom filter `format_money` produces Italian thousand-dot grouping
(12345 → `12.345`) — built manually because locale-based formatting
is brittle across CI/dev machines.

Template directory is resolved at import time via
`Path(__file__).resolve().parents[4] / "packages" / "templates" /
"email"` (repo-root relative). Env is cached across calls.

---

## 3. OutreachAgent rewrite (`src/agents/outreach.py`)

```python
class OutreachInput(BaseModel):
    tenant_id: str
    lead_id: str
    channel: OutreachChannel = OutreachChannel.EMAIL
    force: bool = False

class OutreachOutput(BaseModel):
    lead_id: str
    campaign_id: str | None
    provider_id: str | None            # Resend message id
    status: str                         # pending|sent|failed|cancelled
    cost_cents: int
    skipped: bool
    reason: str | None
```

Flow:

```
load lead + subject + roof + tenant
    ↓
if lead.outreach_sent_at and not force        → skip (already_sent)
    ↓
if subject.pii_hash in global_blacklist       → skip + lead.status=blacklisted
    ↓
if channel == postal                           → skip (postal_not_implemented)
    ↓
resolve recipient:
    B2B: decision_maker_email + decision_maker_email_verified=true
    B2C: always None (Sprint 8 postal)
    → if None, record campaigns(status=failed, failure_reason=no_verified_email)
    ↓
optional: Claude one-sentence opener (prompt varies B2B vs B2C)
    ↓
render_outreach_email(OutreachContext(...))
    ↓
send_email(...)  →  Resend message id
    ↓
INSERT campaigns (status=sent, email_message_id, cost_cents=1)
UPDATE leads SET outreach_channel='email', outreach_sent_at=now(),
    pipeline_status='sent'
INSERT api_usage_log (provider='resend', cost_cents=1)
emit lead.outreach_sent event
```

Degradation:
- Claude opener fails → opener = None; template omits the paragraph.
- Resend 4xx → `_record_failure` inserts `campaigns.status='failed'`
  with a trimmed error in `failure_reason` so the dashboard can
  surface it. Lead pipeline stays at `new`.
- Resend 5xx → bubbles up from the service's own tenacity retry; arq
  worker retry picks it up later.
- Blacklist lookup fails → **fail closed** (treated as blacklisted);
  better to delay a legitimate send than risk spamming an opt-out.

### From address

```python
def _build_from_address(tenant_row) -> str:
    # "{email_from_name or business_name} <outreach@{email_from_domain or solarlead.it}>"
```

Platform fallback is `SolarLead <outreach@solarlead.it>` so the send
still works before the tenant finishes DNS/DKIM verification.

### Opener prompt

Tiny, Italian-first prompt that varies by subject type: B2B references
ATECO description + tenant name; B2C references `postal_city`. Hard-
capped at 25 words, no salutation/signature (those are already in the
template), no explicit "pannelli solari" mention (we talk about the
effect, not the product). Output trimmed at the first newline.

---

## 4. TrackingAgent rewrite (`src/agents/tracking.py`)

Consumes the normalised `EmailEvent` and projects it onto both the
`leads` row and the `campaigns` row via two **pure** functions:

```python
def project_resend_lead_update(*, event_type, current_status, occurred_at)
    -> dict[str, Any]:
    # - delivered  → outreach_delivered_at + pipeline_status='delivered'
    # - opened     → outreach_opened_at    + pipeline_status='opened'
    # - clicked    → outreach_clicked_at   + pipeline_status='clicked'
    # - bounced    → pipeline_status='blacklisted' (terminal, overrides)
    # - complained → pipeline_status='blacklisted'
    # - sent / delivery_delayed → no-op
    # Monotonic: an 'opened' event NEVER regresses 'clicked'.

def project_resend_campaign_update(event_type) -> dict[str, Any]:
    # delivered  → status='delivered'
    # bounced    → status='failed', failure_reason='bounced'
    # complained → status='failed', failure_reason='complained'
```

Pipeline rank map:

| status        | rank |
|---------------|------|
| new           | 0    |
| sent          | 1    |
| delivered     | 2    |
| opened        | 3    |
| clicked       | 4    |
| engaged       | 5    |
| whatsapp      | 6    |
| appointment   | 7    |
| closed_won    | 8    |
| blacklisted   | 99 (terminal) |

Agent `execute`:

1. Dedupe on Svix id — if an `events` row exists with
   `payload->>svix_id = event.id`, skip.
2. Lookup `campaigns WHERE email_message_id = event.email_id`; if
   none → emit `tracking.resend.orphan_<type>` and return.
3. Apply the two projections (if non-empty).
4. On `bounced` / `complained`: fetch subject's `pii_hash` and
   enqueue `compliance_task` with reason `bounce_hard` / `complaint`.
   ComplianceAgent then upserts the global blacklist and cancels any
   pending follow-up campaigns for that subject.
5. Insert the audit `events` row — this doubles as the dedupe marker.

---

## 5. Webhook route (`src/routes/webhooks.py`)

`POST /webhooks/resend` now:

1. Reads the raw body **once** (used both for signature + dispatch).
2. If `RESEND_WEBHOOK_SECRET` is unset → accept but don't process
   (dev mode — keeps Resend happy without triggering retries).
3. Requires `svix-id`, `svix-timestamp`, `svix-signature` headers
   (`400` if missing) and verifies them via
   `verify_webhook_signature`. Invalid → `401`.
4. Parses the JSON, stuffs the Svix id into the envelope so the
   tracking agent can dedupe without re-reading headers.
5. Enqueues `tracking_task` with `job_id=f"tracking:resend:{svix_id}"`.

Unauthenticated traffic never reaches the agent layer.

---

## 6. Dashboard routes (`src/routes/leads.py`)

```
POST /leads/:id/send-outreach?channel=email&force=false
POST /leads/send-outreach-batch?tier=hot&only_new=true&limit=200
```

Both enqueue `outreach_task` with deterministic job ids
(`outreach:{tenant}:{lead}:{channel}`) so duplicate clicks collapse
into one worker run. The batch route defaults to `only_new=true` so
re-clicking "send this week's campaign" never re-spams leads that
already received email.

---

## 7. Config additions (`src/core/config.py`)

```python
next_public_lead_portal_url: str = Field(
    default="http://localhost:3001", alias="NEXT_PUBLIC_LEAD_PORTAL_URL"
)
next_public_dashboard_url: str = Field(
    default="http://localhost:3000", alias="NEXT_PUBLIC_DASHBOARD_URL"
)
```

`resend_api_key`, `resend_webhook_secret` were already present from
Sprint 0.

---

## 8. Test coverage (all pure, all in-memory)

| File                                 | Tests | Focus                                           |
|--------------------------------------|-------|-------------------------------------------------|
| `tests/test_resend_parser.py`        | 17    | send payload, send response, webhook envelope, Svix signature (rotated keys, stale ts, tampered body, bad fields) |
| `tests/test_email_template_service.py` | 22  | B2B & B2C renders, money filter, subject defaults, opener, brand color, GIF preference |
| `tests/test_tracking_projection.py`  | 16    | delivered/opened/clicked ordering, blacklist is terminal, monotonic ranking, no-op events |
| `tests/test_outreach_helpers.py`     | 21    | recipient resolution (verified vs unverified), greeting composition, from-address fallback, slug URL builders |

Run summary:

```
$ pytest tests/
236 passed in 1.38s
```

All 236 tests across all sprints stay green.

---

## 9. What's **not** in this sprint (explicit scope)

- **B2C postal outreach** (PDF postcard + Pixartprinting API) → Sprint 8.
- **Follow-up email sequence** (step-2 and step-3 after N days silence)
  → Sprint 7 (cron + `sequence_step` in `campaigns`).
- **Dashboard UI for outreach controls** → Sprint 7 (dashboard work).
- **WhatsApp outreach** → Sprint 9.
- **Dedicated Lead Portal** (landing page for each public_slug) →
  already scaffolded as `apps/lead-portal`, polished in Sprint 7.

---

## 10. Files touched

```
apps/api/src/services/resend_service.py                (+289 new)
apps/api/src/services/email_template_service.py        (+179 new)
apps/api/src/agents/outreach.py                        (stub → full pipeline)
apps/api/src/agents/tracking.py                        (stub → full pipeline)
apps/api/src/routes/webhooks.py                        (resend endpoint)
apps/api/src/routes/leads.py                           (+2 outreach routes)
apps/api/src/core/config.py                            (+2 frontend URL settings)

packages/templates/email/_base.html.j2                 (new)
packages/templates/email/outreach_b2b.html.j2          (new)
packages/templates/email/outreach_b2c.html.j2          (new)
packages/templates/email/outreach_b2b.txt.j2           (new)
packages/templates/email/outreach_b2c.txt.j2           (new)

apps/api/tests/test_resend_parser.py                   (new, 17 tests)
apps/api/tests/test_email_template_service.py          (new, 22 tests)
apps/api/tests/test_tracking_projection.py             (new, 16 tests)
apps/api/tests/test_outreach_helpers.py                (new, 21 tests)
```

---

## 11. Operational checklist (to do before first prod send)

- [ ] Create the Resend project and copy `RESEND_API_KEY` into env.
- [ ] Configure `RESEND_WEBHOOK_SECRET` (Resend → Webhooks → SVIX secret).
- [ ] Point the Resend webhook at `POST /webhooks/resend`.
- [ ] For every tenant: verify `email_from_domain` DNS (SPF, DKIM,
      DMARC) in Resend before flipping `tenants.status` to `active`.
- [ ] Seed `NEXT_PUBLIC_LEAD_PORTAL_URL` with the production host so
      `lead_url` and `optout_url` in emails point at the real portal.
- [ ] Smoke: send to an internal inbox, verify the `delivered` /
      `opened` webhooks advance the lead in the dashboard.
