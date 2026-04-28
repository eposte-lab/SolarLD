-- ============================================================
-- 0067 — Rendering assets on public CDN
-- ============================================================
-- Sprint 9 Fase A.1.
--
-- ``leads.rendering_gif_url`` and ``leads.rendering_video_url`` today
-- point at Supabase Storage with `getPublicUrl` (which works only when
-- the bucket is public) or signed URLs that expire. Embedding a
-- signed URL inside an outbound email is a deliverability liability:
--   * the URL contains a ?token= query string (Gmail-flag)
--   * URL becomes invalid after expiry, breaking the GIF in archived
--     conversations
--
-- We migrate to a public CDN (Cloudflare R2 + cdn.solarld.app, or
-- Bunny.net as fallback). The video-renderer dual-writes:
--   * Supabase Storage (`renderings` bucket)  — backup, internal use
--   * R2 public bucket                         — delivery for emails
--
-- These two new columns hold the *public* CDN URLs that the email
-- templates always read. Falls back to ``rendering_image_url`` (a
-- static PNG snapshot) if the GIF column is null — we never embed a
-- signed Supabase URL.
--
-- Backfill: ``apps/api/scripts/backfill_cdn_renderings.py`` reads
-- existing ``rendering_gif_url`` rows, downloads from Supabase,
-- uploads to R2, and writes the new column. Idempotent.

BEGIN;

ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS rendering_gif_cdn_url   TEXT,
  ADD COLUMN IF NOT EXISTS rendering_video_cdn_url TEXT;

COMMENT ON COLUMN leads.rendering_gif_cdn_url IS
  'Sprint 9 Fase A.1 — public CDN URL of the GIF rendering used as '
  'inline <img src=…> inside outbound emails. Falls back to '
  'rendering_image_url (static PNG) if null. Populated by the '
  'video-renderer''s dual-write step or by the backfill script.';

COMMENT ON COLUMN leads.rendering_video_cdn_url IS
  'Sprint 9 Fase A.1 — public CDN URL of the MP4 rendering used by '
  'the lead portal video page. Falls back to the existing '
  'rendering_video_url (Supabase signed URL) if null.';

COMMIT;
