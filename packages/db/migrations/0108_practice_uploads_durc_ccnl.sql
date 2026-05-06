-- 0108_practice_uploads_durc_ccnl.sql
--
-- Allow operators to upload DURC (Documento Unico di Regolarità Contributiva)
-- and CCNL (Contratto Collettivo Nazionale Lavoro) documents for a GSE
-- practice. Both kinds are auto-extracted via Claude Vision in
-- `practice_extraction_service.py` (Sprint maggio 2026).

ALTER TABLE practice_uploads
  DROP CONSTRAINT IF EXISTS practice_uploads_upload_kind_check;

ALTER TABLE practice_uploads
  ADD CONSTRAINT practice_uploads_upload_kind_check
    CHECK (upload_kind IN (
      'visura_cciaa',
      'visura_catastale',
      'documento_identita',
      'bolletta_pod',
      'durc',
      'ccnl',
      'altro'
    ));
