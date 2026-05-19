-- 0141 — Consenti i PDF nel bucket `renderings`.
--
-- quote_service.save_quote carica il PDF del preventivo generato nel
-- bucket `renderings` (stessa convenzione di path del creative agent:
-- renderings/{tenant_id}/{lead_id}/...). Il bucket però accettava solo
-- immagini e video → l'upload falliva con 415 invalid_mime_type e
-- l'intera POST /v1/leads/{id}/quotes restituiva 500.
--
-- `renderings` è pubblico, come serve ai PDF dei preventivi. Si
-- aggiunge `application/pdf` alla allowlist (idempotente).

UPDATE storage.buckets
SET allowed_mime_types = array_append(allowed_mime_types, 'application/pdf')
WHERE id = 'renderings'
  AND NOT ('application/pdf' = ANY(allowed_mime_types));
