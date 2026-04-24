-- Sprint 4: snapshot rendering URLs on outreach_sends at send-time.
-- This preserves the exact media that was sent in the email, even if
-- the lead is re-rendered afterwards.
ALTER TABLE outreach_sends
  ADD COLUMN IF NOT EXISTS rendering_gif_url  text,
  ADD COLUMN IF NOT EXISTS rendering_video_url text;

COMMENT ON COLUMN outreach_sends.rendering_gif_url   IS 'GIF URL snapshotted from leads.rendering_gif_url at send time';
COMMENT ON COLUMN outreach_sends.rendering_video_url IS 'MP4 URL snapshotted from leads.rendering_video_url at send time';
