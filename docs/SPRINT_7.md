# Sprint 7 — Lead Portal + Follow-up sequence + opt-out (Delivered)

**Duration**: Weeks 15-16
**Goal**: Close the loop on the outreach channel. Once a lead is sent a
day-0 email (Sprint 6), the platform now:

1. **Serves the recipient** a polished public page
   (`/lead/:public_slug`) with dossier, ROI, WhatsApp CTA, and an
   in-page "request a site visit" form.
2. **Tracks engagement** (visit, WhatsApp click, appointment) via
   idempotent public endpoints, nudging the pipeline forward.
3. **Nudges silent leads** automatically — a daily arq cron enqueues
   hand-written follow-up emails at day 4 (step 2) and day 11
   (step 3) via the existing OutreachAgent, strictly deduped on
   `(lead_id, sequence_step)`.
4. **Handles opt-out** one-click from any follow-up footer →
   blacklists the recipient across every tenant via the existing
   ComplianceAgent.
5. **Purges stale data** nightly — leads older than 24 months are
   deleted to meet the GDPR retention promise.

Every new surface is idempotent, every decision is a pure function, and
the Lead Portal never blocks on analytics pings.

---

## 1. Follow-up rules (`src/services/followup_service.py`)

A single pure function — `select_next_step(candidate, *, now)` — decides
whether to send a follow-up and which step. All side effects live in
the cron (DB read + enqueue).

### Dataclasses

```python
@dataclass(slots=True, frozen=True)
class CampaignSummary:
    sequence_step: int
    status: str                       # pending|sent|delivered|failed|cancelled
    sent_at: datetime | None
    channel: str = "email"

@dataclass(slots=True, frozen=True)
class FollowUpCandidate:
    lead_id: str
    tenant_id: str
    pipeline_status: str
    outreach_channel: str | None
    outreach_sent_at: datetime | None         # day-0 anchor
    campaigns: tuple[CampaignSummary, ...] = ()

@dataclass(slots=True, frozen=True)
class FollowUpDecision:
    should_send: bool
    step: int | None = None                    # 2 or 3 when should_send
    reason: str | None = None                  # when should_send=False
```

### Cadence constants

```python
STEP_2_DELAY_DAYS = 4
STEP_3_DELAY_DAYS = 11
MIN_GAP_BETWEEN_STEPS_DAYS = 3    # guards against clock skew / backfill
```

### Gate ordering (first failure wins)

| # | Gate                                | Reason emitted on skip     |
|---|-------------------------------------|----------------------------|
| 1 | `outreach_channel == "email"`       | `channel_not_email`        |
| 2 | `outreach_sent_at is not None`      | `no_initial_send`          |
| 3 | status not in ENGAGED_STATES        | `lead_engaged_or_terminal` |
| 4 | status in {sent, delivered}         | `status_ineligible`        |
| 5 | step-1 campaign row exists          | `no_step1_campaign`        |
| 6 | step-1 status in {sent, delivered}  | `step1_not_delivered`      |

ENGAGED_STATES = `{opened, clicked, engaged, whatsapp, appointment,
closed_won, closed_lost, blacklisted}` — any forward movement halts the
sequence. The installer takes over from there.

### Step selection

```
step 2:  age_days >= STEP_2_DELAY_DAYS AND no prior step-2 row
step 3:  age_days >= STEP_3_DELAY_DAYS
         AND step-2 was delivered/sent
         AND (now - step2.sent_at) >= MIN_GAP_BETWEEN_STEPS_DAYS
         AND no prior step-3 row
```

Skip reasons surface as telemetry (`too_early_for_stepN`,
`step2_not_delivered`, `step2_too_recent:Nd`, `sequence_complete`).

### Adapter

`build_candidate_from_rows(lead, campaigns)` parses raw Supabase rows
(with ISO `Z`-suffix timestamps) into the dataclass. Tolerates `None`
for everything; the gates take care of the rest.

### Test coverage (`tests/test_followup_selector.py`)

23 pure assertions (parametrize expanded) — channel & anchor gates,
step-2 cadence + dedupe, step-3 cadence + min-gap + pending/failed
step-2 guards, ISO timestamp parsing, null-tolerance. Zero fixtures,
zero network.

---

## 2. OutreachAgent extension — step-aware sends

`OutreachInput` gained a single optional field:

```python
sequence_step: int = Field(default=1, ge=1, le=3)
```

### Changes in the agent pipeline

| Concern                 | step 1 (initial)                                   | step 2 / 3 (follow-up)                                                 |
|-------------------------|----------------------------------------------------|------------------------------------------------------------------------|
| Idempotency key         | `leads.outreach_sent_at` + `force` flag            | `SELECT 1 FROM campaigns WHERE (lead_id, sequence_step=N)`             |
| Claude opener           | One-shot personalised sentence                     | **Skipped** — hand-written copy reads more natural on the 2nd/3rd hit  |
| Template stem           | `outreach_{tier}_v1`                               | `outreach_{tier}_v1_step{N}` (falls back to step 1 if file missing)    |
| Subject default         | `"Il rendering del vostro impianto – {tenant}"`    | `"Promemoria: il rendering è pronto – {tenant}"` (step 2/3 variants)   |
| Pipeline update         | `status → sent`, `outreach_sent_at = now()`        | **No pipeline bump** — lets delivered/opened/clicked webhooks progress |
| Event type              | `lead.outreach_sent`                               | `lead.followup_sent_step{N}`                                           |
| arq job id              | `outreach:{tenant}:{lead}:{channel}`               | `outreach:{tenant}:{lead}:email:step{N}`                               |

**Why the pipeline isn't bumped on step 2/3**: the PipelineMonotonic
rule (Sprint 6) would otherwise regress an `opened` lead back to `sent`
when the cron fires. By only persisting a new `campaigns` row we keep
the webhook-driven progression intact.

---

## 3. Follow-up templates (`packages/templates/email/`)

Four new files, matching the Sprint 6 naming scheme
(`outreach_{tier}[_step{N}].html.j2` / `.txt.j2`):

| File                              | Tone                                                              |
|-----------------------------------|-------------------------------------------------------------------|
| `outreach_b2b_step2.html.j2`      | "Il rendering di **{business_name}** è ancora disponibile"        |
| `outreach_b2b_step3.html.j2`      | "Ultima volta che vi scriviamo — rendering ancora consultabile"   |
| `outreach_b2c_step2.html.j2`      | "Il rendering della vostra casa vi aspetta, {owner_first_name}"   |
| `outreach_b2c_step3.html.j2`      | "Ultimo promemoria per il rendering del vostro impianto"          |

Each shares the step-1 shell — brand colour header, hero rendering,
ROI grid, CTA button, footer with the tenant address and the one-click
unsubscribe link (`APP_URL/optout/{public_slug}`). The `.txt.j2`
twins are narrower (no ROI grid, plain links) and mirror the HTML copy
exactly for the accessibility + deliverability win.

`email_template_service._template_stem_for(subject_type, sequence_step)`
tries `…_step2` / `…_step3` via `env.list_templates()` and falls back to
step 1 silently — so a tenant that hasn't customised step 3 yet still
gets *some* email (never crashes the cron).

---

## 4. arq cron (`src/workers/cron.py` + `workers/main.py`)

Two jobs registered on `WorkerSettings.cron_jobs`:

```python
cron_jobs = [
    cron(follow_up_cron, hour=7, minute=30, run_at_startup=False),
    cron(retention_cron, hour=3, minute=15, run_at_startup=False),
]
```

### `follow_up_cron` (07:30 UTC daily)

```
1. Coarse SQL:
     SELECT id, tenant_id, pipeline_status, outreach_channel, outreach_sent_at
     FROM leads
     WHERE outreach_channel = 'email'
       AND pipeline_status IN ('sent','delivered')
       AND outreach_sent_at <= now() - interval '4 days'
     ORDER BY outreach_sent_at
     LIMIT 500;                                  -- FOLLOW_UP_BATCH_SIZE

2. For each row: load campaigns(sequence_step, status, sent_at, channel).
3. candidate = build_candidate_from_rows(lead, campaigns)
4. decision  = select_next_step(candidate, now=now)
5. if decision.should_send:
       enqueue("outreach_task", {...sequence_step=decision.step...},
               job_id=f"outreach:{tenant}:{lead}:email:step{step}")
6. Log aggregated skip reasons as structured event cron.followup.done
```

The batch size ceiling keeps a single tick bounded. If the backlog
exceeds 500 leads (only possible on first deploy after Sprint 6), the
next day's tick drains another slice.

### `retention_cron` (03:15 UTC daily)

GDPR data-minimisation. Deletes any `leads` row whose `created_at` is
older than `RETENTION_DAYS = 24 * 30` (730). `ON DELETE CASCADE` on
`subjects`/`campaigns`/`events` handles the rest. Storage-bucket purge
(renderings) is deferred to a later sprint — it needs the admin API
rather than SQL.

---

## 5. Public routes (`src/routes/public.py`)

Single router, **no auth**, mounted at `/v1/public`. All handlers are
idempotent — pre-fetchers (Gmail image proxy, antivirus scanners) and
bored human refreshers can hit any endpoint N times without doubling
the effect.

| Method & path                                | Behaviour                                                              |
|----------------------------------------------|------------------------------------------------------------------------|
| `GET /lead/{slug}`                           | Sanitised lead + branded tenant info. `410 Gone` if blacklisted.       |
| `POST /lead/{slug}/visit`                    | Sets `dashboard_visited_at` (only if null), bumps pipeline → `engaged` from silent states. |
| `POST /lead/{slug}/whatsapp-click`           | Sets `whatsapp_initiated_at` (once), bumps pipeline → `whatsapp`.      |
| `POST /lead/{slug}/appointment`  (**202**)   | Validates `AppointmentRequest` payload, sets pipeline → `appointment`, emits event for dashboard realtime. |
| `POST /lead/{slug}/optout`                   | Enqueues `compliance_task` with `BlacklistReason.USER_OPTOUT`; dedupe job id `compliance:{pii_hash}:user_optout`. Returns `{already: bool}` so the portal knows which copy to render. |

`AppointmentRequest` uses a cheap regex for optional email rather than
pydantic's `EmailStr` (which would pull the `email-validator` dep for
audit-log data only). `contact_name` + `phone` are required; `notes` is
capped at 1000 chars.

All handlers emit an `events` row (`route.public` source) via a
best-effort helper `_emit_public_event` — the insert never fails the
HTTP reply.

---

## 6. Lead Portal (`apps/lead-portal`)

Next.js 15 App Router, Tailwind, React 19 — the public-facing surface.

### `src/lib/api.ts`

Shared types + helpers:

```ts
export type LeadFetchResult =
  | { kind: 'ok'; lead: PublicLead }
  | { kind: 'not_found' }
  | { kind: 'gone' };               // 410 from the API → redirect to /optout

export async function fetchPublicLead(slug: string): Promise<LeadFetchResult>;
export function formatEuro(value: number | null): string;
export function formatYears(value: number | null): string;
export function whatsappUrl(number: string | null, preset: string): string | null;
export function leadHeroCopy(lead: PublicLead): { title: string; subtitle: string };
```

`leadHeroCopy` picks the title/subtitle wording based on
`subjects.type` (B2B → "Ecco il rendering per {business_name}" vs B2C →
"Ciao {owner_first_name}, ecco il tuo impianto").

### `/lead/[slug]/page.tsx` (server component)

- `fetchPublicLead(slug)` → `kind === 'gone'` → `redirect('/optout/{slug}?already=1')`.
- `kind === 'not_found'` → `notFound()`.
- Otherwise renders:
  * Brand-colored top border.
  * Hero rendering image + copy from `leadHeroCopy`.
  * 4-card ROI grid: `kWp`, `risparmio annuo`, `rientro`, `CO₂`.
  * Side-by-side: `WhatsAppCta` + `AppointmentForm`.
  * Always-visible `<VisitTracker slug={slug} />` for the fire-and-forget visit ping.

### Client components

| File                       | What it does                                                                                   |
|----------------------------|-------------------------------------------------------------------------------------------------|
| `VisitTracker.tsx`         | `useEffect` + `navigator.sendBeacon` (fallback to `fetch({keepalive: true})`). `useRef` dedupes inside the same tab. |
| `WhatsAppCta.tsx`          | Brand-colored deep link. `onClick` fires a beacon to `/whatsapp-click` before the browser navigates. |
| `AppointmentForm.tsx`      | Client form with `'idle' \| 'submitting' \| 'success' \| 'error'` state machine, brand-colored button, success banner. |
| `optout/[slug]/OptoutConfirm.tsx` | One-click confirm. Treats HTTP 404 as "already done" so a stale slug still produces a friendly success screen. |

### `/optout/[slug]/page.tsx`

Server component — reads `?already=1` from `searchParams`. When
`already=1` (redirected from a `410 Gone`), renders a static "we've
already registered your request" message without the confirm button;
otherwise renders the confirm button inside `<OptoutConfirm>`.

---

## 7. Event surface

New event types introduced in Sprint 7 (stored on `events` table):

| type                           | Source         | When                                  |
|--------------------------------|----------------|---------------------------------------|
| `lead.portal_visited`          | `route.public` | First hit on `/lead/:slug`            |
| `lead.whatsapp_click`          | `route.public` | Click on WhatsApp CTA                 |
| `lead.appointment_requested`   | `route.public` | Submission of portal form             |
| `lead.optout_requested`        | `route.public` | Click on `Conferma la disiscrizione`  |
| `lead.followup_sent_step2`     | `agent.outreach` | step-2 email sent                   |
| `lead.followup_sent_step3`     | `agent.outreach` | step-3 email sent                   |

The dashboard already subscribes to the `events` channel via Supabase
Realtime (Sprint 3), so these arrive in the installer's feed with zero
dashboard-side changes.

---

## 8. Degradation rules

| Failure                                  | Behaviour                                                                        |
|------------------------------------------|----------------------------------------------------------------------------------|
| Resend 5xx on step 2/3                   | `campaigns` row written with `status='failed'`. Cron skips subsequent steps (gate #6). |
| Supabase read timeout in cron            | arq retries (exponential). Partial batch is fine — tomorrow's tick catches up.    |
| Template file missing for step 3         | Silent fallback to step 1 template via `env.list_templates()`. Still sends.      |
| Lead opens step 2 after it's enqueued    | Harmless — pipeline reaches `opened`; step 3 gate #3 blocks further sends.       |
| Bot pre-fetches `/optout/:slug`          | Idempotent — `compliance:{pii_hash}:user_optout` job collapses; 2nd POST returns `already=true`. |
| Gmail proxy hits `/lead/:slug/visit`     | Only bumps `dashboard_visited_at` from NULL; subsequent pings are no-ops.        |
| `email-validator` not installed          | Not required — we use a plain regex in `AppointmentRequest`.                     |
| Follow-up cron runs twice (DST, retry)   | OutreachAgent dedupes on `(lead_id, sequence_step)` at the DB layer; the 2nd run is a no-op. |

---

## 9. Pure-function test coverage

| Suite                              | Asserts | What it nails down                                                 |
|------------------------------------|--------:|--------------------------------------------------------------------|
| `test_followup_selector.py` (NEW)  | 23      | All gates, step-2 + step-3 cadence, min-gap, dedupe, ISO parsing   |
| `test_resend_parser.py`            | 17      | (Sprint 6) Svix signature + event projection                       |
| `test_email_template_service.py`   | 22      | (Sprint 6) Template rendering, fallback                            |
| `test_tracking_projection.py`      | 16      | (Sprint 6) Pipeline monotonicity                                   |
| `test_outreach_helpers.py`         | 21      | (Sprint 6) Recipient resolution, subject defaults                  |
| All prior sprints                  | 160     |                                                                    |

**Total: 259 tests passing in 1.51s.**

The selector never imports Supabase/Redis; the cron is a thin adapter
around the selector. Any logic regression is caught at the unit-test
layer in milliseconds.

---

## 10. Operational checklist

1. **Deploy**: the arq worker image now includes `workers/cron.py` —
   verify the process starts with `arq src.workers.main.WorkerSettings`
   and logs `cron.followup.done` once at 07:30 UTC the day after the
   deploy.
2. **Environment**: no new secrets required. `RESEND_API_KEY` +
   Supabase service key from Sprint 6 are sufficient.
3. **Supabase**: no schema changes — `campaigns.sequence_step` already
   existed from Sprint 3.
4. **Feature flag**: none. The cron is gated by the coarse SQL
   (`pipeline_status IN ('sent','delivered')`), so tenants without any
   Sprint 6 outreach simply get an empty batch.
5. **Monitoring**: watch `cron.followup.done.queued` and
   `cron.followup.done.skipped_reasons` on the structured log stream
   for the first week — unexpectedly high `step1_not_delivered` counts
   often indicate a Resend configuration drift.
6. **Rollback**: removing the cron entry in `workers/main.py` and
   redeploying halts all follow-ups without touching live data. The
   public routes and the Lead Portal remain safe to keep serving.

---

## Out of scope (next up)

- Postal follow-up for B2C leads (Sprint 8).
- Dashboard UI for the per-lead funnel drill-down (Sprint 8+).
- Storage-bucket retention sweep (Sprint 9).
- A/B subject-line experimentation (Sprint 10).
- Per-tenant follow-up cadence override (Sprint 10).
