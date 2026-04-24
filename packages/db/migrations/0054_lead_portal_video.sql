-- Sprint 4: stable video slug for each lead's portal video page.
-- The slug is auto-generated on insert so every lead gets one even if
-- the rendering hasn't run yet (the page handles the null video_url gracefully).
ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS portal_video_slug text
    DEFAULT replace(gen_random_uuid()::text, '-', '');

CREATE UNIQUE INDEX IF NOT EXISTS leads_portal_video_slug_idx
  ON leads (portal_video_slug)
  WHERE portal_video_slug IS NOT NULL;

COMMENT ON COLUMN leads.portal_video_slug IS 'Short slug for /lead/[slug]/video landing page (auto-generated UUID without dashes)';
