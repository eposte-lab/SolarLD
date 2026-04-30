-- GSE Practices Module — Livello 2 Sprint 4: customer-document OCR.
--
-- Lets the installer drag-drop documents the customer provided
-- (visura camerale, carta d'identità, visura catastale, bolletta
-- elettrica recente) onto the practice detail page.  Claude Vision
-- extracts structured fields, the dashboard surfaces them next to
-- the MissingDataPanel as "suggestions" the installer can apply with
-- one click — collapses 15 minutes of manual transcription per
-- practice into ~30 seconds of upload + review.
--
-- Layered on 0083_practices.sql.  Mirrors the existing
-- bolletta_uploads pattern (0065) but tenant+practice scoped.
--
--   • storage bucket `practice-uploads` — private, 10 MB / image|pdf
--   • table `practice_uploads` — one row per file:
--       upload_kind  — 'visura_cciaa' | 'visura_catastale' |
--                      'documento_identita' | 'bolletta_pod' | 'altro'
--       extraction_status — 'pending' | 'success' | 'failed' |
--                           'manual_required'
--       extracted_data    — JSONB with fields per kind (see prompts in
--                           practice_extraction_service.py)
--       confidence        — Claude self-reported (0..1)
--       applied_at        — set when installer clicks "Apply
--                           suggestions" → fields written to practice
--                           extras / tenant / subject
--
-- RLS: tenant-scoped, identical pattern to practices.
-- Storage: service-role writes (upload endpoint resolves tenant);
-- tenant operators read their own folder via signed URLs.

BEGIN;

-- ---------------------------------------------------------------------
-- Table: practice_uploads
-- ---------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS practice_uploads (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  practice_id     UUID NOT NULL REFERENCES practices(id) ON DELETE CASCADE,
  uploaded_by     UUID,                                    -- auth.users.id of operator

  -- ── File metadata ──
  storage_path    TEXT NOT NULL,                           -- practice-uploads/{tenant}/{practice}/{uuid}.{ext}
  original_name   TEXT NOT NULL,                           -- "visura_acme_srl.pdf"
  mime_type       TEXT NOT NULL,
  file_size_bytes INTEGER NOT NULL CHECK (file_size_bytes >= 0),

  -- ── Document classification (set on upload, may be updated post-OCR) ──
  upload_kind     TEXT NOT NULL CHECK (upload_kind IN (
    'visura_cciaa',
    'visura_catastale',
    'documento_identita',
    'bolletta_pod',
    'altro'
  )),

  -- ── Claude Vision extraction ──
  extraction_status   TEXT NOT NULL DEFAULT 'pending'
    CHECK (extraction_status IN ('pending','success','failed','manual_required')),
  extracted_data      JSONB NOT NULL DEFAULT '{}'::jsonb,
  confidence          NUMERIC(3,2)
    CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
  extraction_error    TEXT,
  raw_response        JSONB,                               -- model output preserved for re-extraction
  extracted_at        TIMESTAMPTZ,

  -- ── Apply state ──
  -- When the installer clicks "Applica suggerimenti", the extracted
  -- fields are written to practice.extras / tenants / subjects in a
  -- single transaction, and applied_at is set so the UI can show a
  -- ✓ checkmark and disable the button.
  applied_at          TIMESTAMPTZ,
  applied_by          UUID,
  applied_targets     JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {tenant: [...], practice: [...], subject: [...]}

  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Hot path: practice detail page lists uploads for one practice
-- ordered by upload time desc.
CREATE INDEX IF NOT EXISTS idx_practice_uploads_practice
  ON practice_uploads (practice_id, created_at DESC);

-- Tenant-scoped queries (e.g. quota dashboards).
CREATE INDEX IF NOT EXISTS idx_practice_uploads_tenant
  ON practice_uploads (tenant_id, created_at DESC);

-- updated_at trigger (function set_updated_at() exists from 0083).
DROP TRIGGER IF EXISTS trg_practice_uploads_updated ON practice_uploads;
CREATE TRIGGER trg_practice_uploads_updated
  BEFORE UPDATE ON practice_uploads
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------
-- RLS
-- ---------------------------------------------------------------------

ALTER TABLE practice_uploads ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS practice_uploads_tenant_iso ON practice_uploads;
CREATE POLICY practice_uploads_tenant_iso
  ON practice_uploads
  FOR ALL
  TO authenticated
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

-- ---------------------------------------------------------------------
-- Storage bucket: practice-uploads
-- ---------------------------------------------------------------------

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
  'practice-uploads',
  'practice-uploads',
  false,
  10485760,                                               -- 10 MB
  ARRAY[
    'image/jpeg',
    'image/png',
    'image/webp',
    'application/pdf'
  ]
)
ON CONFLICT (id) DO NOTHING;

-- Read: tenant operators can fetch objects under their tenant prefix.
DROP POLICY IF EXISTS "Practice uploads tenant read" ON storage.objects;
CREATE POLICY "Practice uploads tenant read" ON storage.objects
  FOR SELECT
  USING (
    bucket_id = 'practice-uploads'
    AND (
      auth.role() = 'service_role'
      OR (storage.foldername(name))[1] = auth_tenant_id()::text
    )
  );

-- Write/Delete: service role only — the upload endpoint runs as
-- service role after JWT-validating the operator and resolving
-- practice_id → tenant_id.
DROP POLICY IF EXISTS "Practice uploads service write" ON storage.objects;
CREATE POLICY "Practice uploads service write" ON storage.objects
  FOR INSERT
  WITH CHECK (
    bucket_id = 'practice-uploads'
    AND auth.role() = 'service_role'
  );

DROP POLICY IF EXISTS "Practice uploads service delete" ON storage.objects;
CREATE POLICY "Practice uploads service delete" ON storage.objects
  FOR DELETE
  USING (
    bucket_id = 'practice-uploads'
    AND auth.role() = 'service_role'
  );

COMMIT;
