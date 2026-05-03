-- 0096_outreach_sends_rendering_image_url.sql
--
-- Add the static after-image snapshot column on outreach_sends so
-- the /invii detail page can render it as third-tier hero fallback
-- when video + GIF are both null. Migration 0067 (rendering_cdn)
-- added rendering_video_url + rendering_gif_url; this completes the
-- triple so any send row carries the same artefacts the email
-- body actually rendered.
--
-- Without this column, every outreach send fails since the
-- rendering-image fallback commit with PostgREST error PGRST204
-- ("Could not find the 'rendering_image_url' column"). Hot-fixed
-- via Supabase MCP before the deploy lands; this file documents
-- the schema change for future replays.

ALTER TABLE outreach_sends
  ADD COLUMN IF NOT EXISTS rendering_image_url TEXT;

COMMENT ON COLUMN outreach_sends.rendering_image_url IS
  'Snapshot of the static after-image (panel-painted aerial) at send time. Used as third-tier hero fallback on the /invii detail page when rendering_video_url and rendering_gif_url are both null (CREATIVE_SKIP_REPLICATE bypassed Kling, or Remotion failed). Same artefact the email body shows under the gif/image fallback chain.';
