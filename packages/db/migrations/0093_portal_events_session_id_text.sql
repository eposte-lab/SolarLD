-- 0093_portal_events_session_id_text.sql
--
-- Convert portal_events.session_id from UUID to TEXT.
--
-- Bug: every portal_events INSERT since 2026-04-19 has been failing
-- silently. The column is typed UUID NOT NULL but two API callers
-- legitimately need text values:
--
--   1. routes/public.py:portal_track  — receives session_id from the
--      lead-portal client. Although the portal client may emit a
--      crypto.randomUUID() value, the Pydantic model on the server
--      types it as `str`, and any non-UUID format (e.g. timestamped
--      session ID, ad-hoc retry suffix) trips a 22P02 cast error
--      that the route's `except Exception` swallows.
--
--   2. routes/public.py:upload_bolletta — fires a synthetic
--      server-side beacon with `session_id=f"server:{upload_id}"` so
--      the engagement score bumps even if the user closed the tab
--      mid-OCR. That format is intentionally NOT a UUID and was
--      always going to fail under the old constraint.
--
-- Net effect of the bug: two rows ever inserted into the table
-- (2026-04-19), then zero. The dashboard's LeadPortalTimeline reads
-- from this table and consequently shows nothing about portal
-- engagement, even after a prospect opens the link, scrolls, plays
-- the video, and uploads their bolletta.
--
-- Fix: relax the column to TEXT NOT NULL. UUID-formatted values are
-- still accepted unchanged; arbitrary strings now also fit. Rate-
-- limiting in Redis already treats session_id as text, so no
-- downstream changes are needed.

ALTER TABLE portal_events
  ALTER COLUMN session_id TYPE TEXT;

COMMENT ON COLUMN portal_events.session_id IS
  'Opaque session identifier. Lead-portal clients send a crypto.randomUUID(); server-side beacons (bolletta upload, OCR completion) send synthetic strings like "server:{upload_id}". Stored as TEXT to accept both — this column was UUID until migration 0093 caused every INSERT to fail silently.';
