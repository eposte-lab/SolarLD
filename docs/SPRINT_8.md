# Sprint 8 — Dashboard Live (Delivered)

**Duration**: Weeks 17-18
**Goal**: Turn the installer-facing dashboard from static shells into a
live, RLS-scoped, realtime cockpit. After Sprint 8, an installer logs
in and sees:

1. **Four KPIs at a glance** — leads sent in the last 30 days, hot
   leads currently in pipeline, appointments requested this month,
   closed-won count.
2. **The top 10 hot leads** ranked by score, one click away from the
   full dossier.
3. **A searchable/filterable leads table** at `/leads` with server-side
   pagination, tier/status chips, and a last-touch timestamp that knows
   about portal visits.
4. **A full lead dossier** at `/leads/[id]` — rendering, ROI grid,
   anagrafica, tetto, outreach sequence, event timeline, and the
   **"Invia outreach"** button that fires `POST /v1/leads/:id/send-outreach`
   through FastAPI.
5. **A campaigns deck** at `/campaigns` aggregating delivery / open /
   click rates from the raw `campaigns` rows.
6. **A realtime toast stack** fed by Supabase Realtime on the `events`
   table — new `lead.outreach_sent` / `lead.portal_visited` /
   `lead.whatsapp_click` / `lead.appointment_requested` rows pop up in
   the bottom-right within ~1s of being written.

Every read is a server component; every action is a client component
that hits FastAPI with the user's Supabase JWT. No service-role key
ever reaches the browser, and RLS enforces tenant isolation end-to-end.

---

## 1. Read path — Supabase SSR with RLS

The dashboard runs on Next.js 15 App Router. Every page is a
`force-dynamic` async server component that creates a Supabase SSR
client bound to the browser's `sb-…-auth-token` cookie. Because every
query hits RLS with the caller's `auth.uid()`, we never pass
`tenant_id` explicitly — the database scopes it for us via
`auth_tenant_id()` (Sprint 0).

```
Browser cookie ─► Next.js server component
                       │
                       ▼
              createSupabaseServerClient()
                       │
                       ▼   (RLS: auth_tenant_id() = row.tenant_id)
                  PostgREST
                       │
                       ▼
                React tree (SSR HTML)
```

### `src/lib/data/tenant.ts`

`getCurrentTenantContext()` is called at the top of `layout.tsx` and
every page:

```ts
export interface TenantContext {
  tenant: TenantRow;
  role: string;
  user_id: string;
  user_email: string | null;
}

export async function getCurrentTenantContext(): Promise<TenantContext | null>;
```

Joins `tenant_members → tenants` for the current `auth.uid()`. Returns
`null` when the user has no membership — the layout redirects to
`/login`.

### `src/lib/data/leads.ts`

Four functions back the three leads surfaces:

| Fn                          | Used by                   | Shape                                 |
|-----------------------------|---------------------------|----------------------------------------|
| `listLeads({page, filter})` | `/leads`                  | `{rows, total}` — paginated, RLS-scoped |
| `getLeadById(id)`           | `/leads/[id]`             | `LeadDetailRow \| null`                |
| `listTopHotLeads(limit)`    | `/` overview              | `LeadListRow[]` — `score DESC`         |
| `getOverviewKpis()`         | `/` overview              | 4 parallel `count:exact, head:true`    |

`LIST_COLUMNS` and `DETAIL_COLUMNS` are SQL string constants with
nested selects on `subjects` and `roofs` so we pull the whole card in
one round-trip. The return is cast `as unknown as LeadListRow[]` —
Supabase's generated types don't model joined selects precisely
enough, but our stricter row types document the actual shape.

`getOverviewKpis()` issues **four `head: true` count queries in
parallel** (no rows shipped, just a `Content-Range` header). Cheaper
than a GROUP BY at current scale; revisit if any tenant crosses ~100 k
leads.

### `src/lib/data/campaigns.ts`

| Fn                               | Used by            | What it does                               |
|----------------------------------|--------------------|--------------------------------------------|
| `listCampaigns(limit)`           | `/campaigns`       | Last N rows ordered by `sent_at DESC`      |
| `getCampaignDeliveryStats()`     | `/campaigns`       | In-memory funnel — total / delivery / open / click / failed |
| `listCampaignsForLead(lead_id)`  | `/leads/[id]`      | Ordered by `sequence_step ASC`             |
| `listEventsForLead(lead_id)`     | `/leads/[id]`      | Ordered by `created_at DESC` (timeline)    |

Delivery stats are computed from `campaigns.status` +
`delivered_at` / `opened_at` / `clicked_at` nullables in a single
reducer — one query, zero aggregation SQL. Good enough while the per-
tenant row count is under 100 k.

---

## 2. Write path — FastAPI action button

Reads go straight to Supabase, but **writes don't**. The only
dashboard-driven write in Sprint 8 is the "Invia outreach" button, and
it goes through the existing FastAPI route:

```
SendOutreachButton.onClick
        │
        │ api.post('/v1/leads/:id/send-outreach?channel=email[&force=true]', {})
        ▼
Next.js → FastAPI (Authorization: Bearer <supabase_jwt>)
        │
        │ enqueue arq job (deterministic job_id)
        ▼
OutreachAgent (Sprint 6/7) → Resend
```

### `SendOutreachButton.tsx`

Client component, four-state discriminated union:

```ts
type State =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'success'; message: string }
  | { kind: 'error'; message: string };
```

- Primary button is disabled when `alreadySent` is true; a secondary
  "Re-invia (force)" button appears alongside and passes `force=true`.
- On success we schedule `router.refresh()` after 2 s so the server
  components re-fetch with the new pipeline state — no optimistic
  mutation, no drift.
- Errors surface as inline `text-destructive` copy. `ApiError` bodies
  (JSON or string) are flattened so the user sees the backend's
  explanation verbatim.

The backend is still the source of truth on idempotency (the arq
`job_id = outreach:{tenant}:{lead}:{channel}` collapses duplicates),
but the local `busy` flag also guards against double-click on flaky
networks.

### `src/lib/api-client.ts`

A small `fetch` wrapper that:

1. Reads the access token from the browser's Supabase client
   (`supabase.auth.getSession()`).
2. Injects `Authorization: Bearer <jwt>` on every request.
3. Throws a typed `ApiError` on non-2xx so the UI can render
   `(err.status, err.body)` without parsing surprises.

---

## 3. Route map

All routes live under the `(dashboard)` group and share `layout.tsx`.

| Path                    | File                                       | Render   | Purpose                                                    |
|-------------------------|--------------------------------------------|----------|------------------------------------------------------------|
| `/`                     | `(dashboard)/page.tsx`                     | server   | 4 KPI cards + top 10 hot leads                             |
| `/leads`                | `(dashboard)/leads/page.tsx`               | server   | Paginated table + tier/status chip filters                 |
| `/leads/[id]`           | `(dashboard)/leads/[id]/page.tsx`          | server   | Full dossier (ROI, anagrafica, tetto, sequence, timeline)  |
| `/leads/[id]` (action)  | `leads/[id]/SendOutreachButton.tsx`        | client   | POST `/v1/leads/:id/send-outreach`                         |
| `/campaigns`            | `(dashboard)/campaigns/page.tsx`           | server   | Delivery KPIs + last 100 campaigns                         |
| (overlay)               | `components/realtime-toaster.tsx`          | client   | Supabase Realtime → rolling toast stack                    |

Deferred to Sprint 9/10: `/territories`, `/analytics`, `/settings`,
`/onboarding`. The nav links are kept visible (to avoid UI jitter when
they land) but point at empty route stubs.

---

## 4. Filter UI — zero client state

`/leads` is the only screen with interactive filters. To keep it as a
pure server component we encode filter state in query params and
render filter chips as `<Link>`s.

```ts
const queryFor = (overrides: Record<string, string | undefined>) => {
  const params = new URLSearchParams();
  if (filter.tier)   params.set('tier', filter.tier);
  if (filter.status) params.set('status', filter.status);
  if (filter.q)      params.set('q', filter.q);
  if (page > 1)      params.set('page', String(page));
  for (const [k, v] of Object.entries(overrides)) {
    if (v === undefined || v === '') params.delete(k);
    else params.set(k, v);
  }
  return params.toString() ? `/leads?${params}` : '/leads';
};
```

Each `FilterChip` receives `active` + `href`; clicking it is a plain
navigation — no `useState`, no client-side fetch. Pagination works
the same way. This keeps the page cold-cacheable per cookie and
eliminates an entire class of client/server sync bugs.

---

## 5. Realtime toasts

`components/realtime-toaster.tsx` (client component) is rendered once
per layout. It subscribes to a tenant-scoped Postgres Changes channel:

```ts
supabase
  .channel(`events:${tenantId}`)
  .on('postgres_changes', {
    event: 'INSERT',
    schema: 'public',
    table: 'events',
    filter: `tenant_id=eq.${tenantId}`,
  }, (msg) => { ...push toast... })
  .subscribe();
```

### `classify(event_type, payload)`

Pure function, maps raw event types to UI copy + accent colour:

| `event_type`                  | Title                              | Accent   |
|-------------------------------|------------------------------------|----------|
| `lead.outreach_sent`          | "Outreach inviata"                 | sky      |
| `lead.followup_sent_step{N}`  | "Follow-up stepN inviato"          | sky      |
| `lead.portal_visited`         | "Lead ha aperto il portal"         | indigo   |
| `lead.whatsapp_click`         | "Click su WhatsApp"                | indigo   |
| `lead.appointment_requested`  | "Richiesta appuntamento!"          | emerald  |
| `lead.optout_requested`       | "Opt-out ricevuto"                 | zinc     |
| (fallback)                    | raw `event_type`                   | zinc     |

Appointment toasts use `payload.contact_name` as the subtitle when
available; everything else falls back to the raw event type.

### Stack behaviour

- Max 5 concurrent toasts (`.slice(0, 5)`) — newest on top.
- Each toast auto-dismisses after **6 s** via a local `setTimeout`.
- Component is `pointer-events-none` on the container but
  `pointer-events-auto` on individual toasts so the rest of the page
  stays clickable through the margins.

### Publication requirement

The Supabase `supabase_realtime` publication is opt-in per table.
Sprint 8 ships a single-statement migration:

```sql
ALTER PUBLICATION supabase_realtime ADD TABLE public.events;
ALTER TABLE public.events REPLICA IDENTITY FULL;
```

Without the publication the subscribe succeeds silently and no
payloads arrive — the toaster just never shows, which is exactly the
degradation we want (see §7).

RLS still applies to Realtime: subscribers only receive rows whose
`tenant_id` matches `auth_tenant_id()`, enforced by the existing
policies on `events`.

---

## 6. Presentational atoms

Two shared UI modules, both server-safe (no `'use client'`):

### `components/ui/badges.tsx`

| Component    | Inputs                 | Output                                                         |
|--------------|------------------------|----------------------------------------------------------------|
| `TierBadge`  | `tier: LeadScoreTier`  | Coloured pill — HOT (red) / WARM (amber) / COLD (blue) / SCARTATO (zinc) |
| `StatusBadge`| `status: LeadStatus`   | One of 11 status colours via `pipelineLabel()` map             |

`pipelineLabel()` is the single source of truth for pipeline-state
Italian copy; reused on detail page, list page, toasts.

### `components/ui/stat-card.tsx`

A thin `<div>` with `label / value / hint` and a 5-variant `accent`
(`primary | warm | success | hot | zinc`). Drives every KPI on the
overview and campaigns pages.

### `lib/utils.ts` extensions

Five new formatters ship in Sprint 8:

| Fn                  | Sample output         |
|---------------------|------------------------|
| `formatEurPlain(n)` | `"1.240 €"` (`—` for null) |
| `formatNumber(n)`   | Italian locale, grouping separators |
| `formatDate(iso)`   | `"3 apr 2026"`        |
| `relativeTime(iso)` | `"ieri"`, `"3h fa"`, `"2 giorni fa"` |
| `formatPercent(x, d)` | `"64,3%"`           |
| `daysSince(iso)`    | integer days or `null` |

All pure, all null-tolerant — they surface `—` (em-dash) for missing
data rather than crashing.

---

## 7. Degradation rules

| Failure                                   | Behaviour                                                                       |
|-------------------------------------------|---------------------------------------------------------------------------------|
| Supabase JWT expired mid-session          | `getCurrentTenantContext()` returns `null` → layout redirects to `/login`.      |
| RLS denies a read                         | Supabase returns `[]` with no error → the UI renders "Nessun lead trovato".      |
| `supabase_realtime` publication missing   | `.subscribe()` succeeds, no payloads arrive → toaster stays empty. No error.    |
| FastAPI down on "Invia outreach"          | `ApiError` caught; inline red copy shows status + body. Button returns to idle. |
| User double-clicks "Invia outreach"       | Local `busy` flag blocks 2nd click. Backend `job_id` collapses if one leaks.    |
| Lead has no `rendering_image_url`         | Hero section is skipped entirely — no broken-image UI.                          |
| `subjects.business_name` missing (B2C)    | Falls back to `owner_first_name owner_last_name`, then `—`.                     |
| `roi_data` is `null`                      | All four cards render `—`; page still loads.                                    |
| `campaigns.channel` column has odd value  | Renders as uppercase text; status badge falls through to the grey default.      |
| Realtime payload malformed (`row.id` missing) | Toast would crash on key — the `classify` cast assumes the shape; if Realtime ever changes, tighten here. |
| `NEXT_PUBLIC_LEAD_PORTAL_URL` unset       | Defaults to `http://localhost:3001` (dev) — set in production env.              |

---

## 8. Auth + tenant-scoping invariants

The dashboard runs on three nested auth layers, each of which must
hold for a row to reach the UI:

1. **Middleware / layout**: `getCurrentTenantContext()` asserts the
   user has a `tenant_members` row. Missing → `redirect('/login')`.
2. **Row Level Security**: every `select` PostgREST call is gated by
   `auth_tenant_id()` vs. the row's `tenant_id`. Sprint 0 policies.
3. **Realtime channel**: filter `tenant_id=eq.${ctx.tenant.id}` is
   sent server-side; Supabase additionally re-checks RLS on the
   publication before delivering each payload.

A revoked membership (DELETE from `tenant_members`) causes the next
request to redirect, and any open Realtime channel stops receiving
payloads within the cache TTL. No server-side session invalidation
wiring is needed for Sprint 8.

---

## 9. Operational checklist

1. **Migrations applied**:
   - `sprint8_enable_realtime_events` — adds `public.events` to
     `supabase_realtime` and sets `REPLICA IDENTITY FULL`. Idempotent
     at the publication level (re-running it errors loudly but
     harmlessly).
2. **Environment** (dashboard):
   - `NEXT_PUBLIC_SUPABASE_URL`
   - `NEXT_PUBLIC_SUPABASE_ANON_KEY`
   - `NEXT_PUBLIC_LEAD_PORTAL_URL` (prod → portal vercel URL)
   - `NEXT_PUBLIC_API_URL` (FastAPI base for the outreach button)
3. **Demo tenant**: a seed script + SQL block creates one
   `auth.users` row (`bcrypt` password via `crypt()`), one `tenants`
   row, one `tenant_members` link, plus a few leads with renderings so
   the dashboard isn't empty on first visit.
4. **Feature flag**: none. The realtime toaster degrades silently if
   the publication is missing, so a staged roll-out is safe.
5. **Monitoring**: watch browser network for `/realtime/v1/websocket`
   sustained connections; verify toast latency with a manual
   `INSERT INTO events` on staging.
6. **Rollback**: revert `apps/dashboard` to the Sprint 7 commit. The
   publication change is safe to leave — it costs ~0 if nothing
   subscribes.

---

## 10. Files touched

**New**:

```
apps/dashboard/src/types/db.ts
apps/dashboard/src/lib/data/tenant.ts
apps/dashboard/src/lib/data/leads.ts
apps/dashboard/src/lib/data/campaigns.ts
apps/dashboard/src/components/ui/badges.tsx
apps/dashboard/src/components/ui/stat-card.tsx
apps/dashboard/src/components/ui/sign-out-button.tsx
apps/dashboard/src/components/realtime-toaster.tsx
apps/dashboard/src/app/(dashboard)/leads/[id]/SendOutreachButton.tsx
apps/dashboard/src/app/(dashboard)/campaigns/page.tsx
supabase/migrations/…_sprint8_enable_realtime_events.sql
```

**Rewritten**:

```
apps/dashboard/src/app/(dashboard)/layout.tsx    (+ auth guard, nav, toaster mount)
apps/dashboard/src/app/(dashboard)/page.tsx       (overview KPIs + hot leads)
apps/dashboard/src/app/(dashboard)/leads/page.tsx (filters + pagination)
apps/dashboard/src/app/(dashboard)/leads/[id]/page.tsx (full dossier)
apps/dashboard/src/lib/utils.ts                   (formatters)
```

---

## Out of scope (next up)

- `/territories` heat-map + lead density per CAP (Sprint 9).
- `/analytics` cohort funnel by source / tier / week (Sprint 9).
- `/settings` tenant branding + email sender domain management (Sprint 9).
- `/onboarding` wizard for first-run installers (Sprint 10).
- Free-text search on joined `subjects.business_name` /
  `owner_last_name` (currently falls back to `public_slug`).
- Optimistic mutation on the outreach button (replace `router.refresh`
  with a targeted revalidate tag once Next.js 15 caching rules settle).
- A/B subject-line editor UI surface (Sprint 10, alongside backend
  experimentation).
