-- ============================================================
-- 0065 — Bolletta uploads (utility-bill OCR + manual entry)
-- ============================================================
-- Sprint 8 Fase B.1.
--
-- Stores utility bills uploaded by leads from the public portal
-- (signed link page) plus an OCR readout (Claude Vision) and a
-- manually-edited override for when the model gets it wrong.
--
-- Why a dedicated table and not a JSONB blob on `leads`:
--   * one lead can upload more than one bill across the season
--     (yearly comparison) — we want the history, not the last value
--   * each row is a separate point in time we may need to audit
--     (storage_path, mime_type, file_size_bytes are forensic data
--     for support tickets / GDPR deletion requests)
--   * the OCR provider response is a JSONB we want to keep raw, so
--     we can re-extract if we change models without re-uploading
--
-- The upload endpoint uses the service-role client (slug → tenant_id
-- resolution happens before insert), so RLS just needs to enforce
-- "authenticated tenant operator can only see their own rows" — same
-- pattern as other tenant-scoped tables.

BEGIN;

CREATE TABLE IF NOT EXISTS bolletta_uploads (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  lead_id         UUID NOT NULL REFERENCES leads(id) ON DELETE CASCADE,

  -- Storage object: ``bollette/{tenant_id}/{lead_id}/{uuid}.{ext}``
  storage_path    TEXT NOT NULL,
  mime_type       TEXT NOT NULL,
  file_size_bytes INTEGER NOT NULL CHECK (file_size_bytes >= 0),

  -- ---- OCR readout (nullable until job completes / on manual_only)
  ocr_kwh_yearly      NUMERIC(10, 2),
  ocr_eur_yearly      NUMERIC(10, 2),
  ocr_provider        TEXT,
  ocr_confidence      NUMERIC(3, 2)
    CHECK (ocr_confidence IS NULL OR ocr_confidence BETWEEN 0 AND 1),
  ocr_raw_response    JSONB,
  ocr_error           TEXT,

  -- ---- Manual override (filled when the user corrects OCR or
  --      enters numbers without uploading a bill).
  manual_kwh_yearly   NUMERIC(10, 2),
  manual_eur_yearly   NUMERIC(10, 2),

  -- Source flag matters for analytics: 'upload_ocr' = high-trust
  -- (OCR succeeded), 'upload_manual' = OCR ran but user corrected,
  -- 'manual_only' = no file uploaded, user typed numbers in.
  source              TEXT NOT NULL
    CHECK (source IN ('upload_ocr', 'upload_manual', 'manual_only')),

  uploaded_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Latest upload per lead is the dashboard hot path — index on
-- (lead_id, uploaded_at DESC) keeps the SELECT covered.
CREATE INDEX IF NOT EXISTS idx_bolletta_uploads_lead
  ON bolletta_uploads (lead_id, uploaded_at DESC);

-- Tenant-scoped queries (operator dashboard "my bills") need this:
CREATE INDEX IF NOT EXISTS idx_bolletta_uploads_tenant
  ON bolletta_uploads (tenant_id, uploaded_at DESC);

ALTER TABLE bolletta_uploads ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS bolletta_uploads_tenant_iso ON bolletta_uploads;
CREATE POLICY bolletta_uploads_tenant_iso
  ON bolletta_uploads
  FOR ALL
  TO authenticated
  USING (tenant_id = auth_tenant_id())
  WITH CHECK (tenant_id = auth_tenant_id());

-- Convenience column on `leads`: timestamp of last bolletta uploaded.
-- Lets the dashboard filter "lead has uploaded a bill" without a JOIN.
-- (The actual uploads live in bolletta_uploads — this is a denormalised
-- read-shortcut, set by the upload endpoint at the same write.)
ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS bolletta_uploaded_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_leads_bolletta_uploaded_at
  ON leads (bolletta_uploaded_at DESC NULLS LAST)
  WHERE bolletta_uploaded_at IS NOT NULL;

-- ============================================================
-- Storage bucket: ``bollette`` (private, service-role writes only).
-- Signed URLs from the API give operators short-lived read access.
-- ============================================================
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
  'bollette',
  'bollette',
  false,
  10485760,  -- 10 MB cap (PDFs scanned at 300dpi run ~2-4MB)
  ARRAY[
    'image/jpeg',
    'image/png',
    'image/webp',
    'application/pdf'
  ]
)
ON CONFLICT (id) DO NOTHING;

-- Read: tenant operators can fetch objects under their tenant prefix.
DROP POLICY IF EXISTS "Bollette tenant read" ON storage.objects;
CREATE POLICY "Bollette tenant read" ON storage.objects
  FOR SELECT
  USING (
    bucket_id = 'bollette'
    AND (
      auth.role() = 'service_role'
      OR (storage.foldername(name))[1] = auth_tenant_id()::text
    )
  );

-- Write: service role only. The public upload endpoint (slug-scoped)
-- runs as service role after validating the slug → tenant_id mapping;
-- no path lets an operator JWT or an anonymous JWT write here.
DROP POLICY IF EXISTS "Bollette service write" ON storage.objects;
CREATE POLICY "Bollette service write" ON storage.objects
  FOR INSERT
  WITH CHECK (
    bucket_id = 'bollette'
    AND auth.role() = 'service_role'
  );

DROP POLICY IF EXISTS "Bollette service delete" ON storage.objects;
CREATE POLICY "Bollette service delete" ON storage.objects
  FOR DELETE
  USING (
    bucket_id = 'bollette'
    AND auth.role() = 'service_role'
  );

COMMIT;
