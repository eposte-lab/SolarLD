-- ============================================================
-- 0064 — tenant "Chi siamo" / About narrative
-- ============================================================
-- Sprint 8 Fase A.2 — installer-controlled narrative + identity
-- fields surfaced on the public lead portal "About" section.
--
-- Why these columns and not a free-form jsonb blob:
--   - about_md is rendered through react-markdown with a sanitize
--     allow-list, so it must be a real text column with byte-level
--     length budgeting (DB CHECK enforces 4 KB).
--   - year_founded / team_size / certifications / hero / tagline are
--     each surfaced individually in the portal layout (chips, badges,
--     hero image), so denormalising them as columns keeps the SELECT
--     simple and avoids JSON path churn from the public endpoint.
--
-- All columns nullable to keep migration backwards-compatible: an
-- installer who never opens /settings/branding/about still sees the
-- portal render fine (the AboutSection just hides itself).
--
-- No new RLS — `tenants` already has an owner-only policy; these
-- columns inherit it.

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS about_md             TEXT,
  ADD COLUMN IF NOT EXISTS about_year_founded   INTEGER,
  ADD COLUMN IF NOT EXISTS about_team_size      TEXT,
  ADD COLUMN IF NOT EXISTS about_certifications TEXT[]
    NOT NULL DEFAULT ARRAY[]::TEXT[],
  ADD COLUMN IF NOT EXISTS about_hero_image_url TEXT,
  ADD COLUMN IF NOT EXISTS about_tagline        TEXT;

-- Cap markdown length at 4 KB hard. The dashboard editor enforces the
-- same limit client-side, but we trust the API not the UI.
ALTER TABLE tenants
  ADD CONSTRAINT tenants_about_md_length_chk
    CHECK (about_md IS NULL OR octet_length(about_md) <= 4096);

ALTER TABLE tenants
  ADD CONSTRAINT tenants_about_tagline_length_chk
    CHECK (about_tagline IS NULL OR char_length(about_tagline) <= 120);

-- Sanity range on year — keeps weird inputs out without making the
-- field business-critical (we never reason on it numerically).
ALTER TABLE tenants
  ADD CONSTRAINT tenants_about_year_founded_range_chk
    CHECK (
      about_year_founded IS NULL
      OR (about_year_founded BETWEEN 1900 AND 2100)
    );
