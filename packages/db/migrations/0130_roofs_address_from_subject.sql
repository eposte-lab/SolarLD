-- 0130_roofs_address_from_subject.sql
--
-- Backfill di roofs.address. Il funnel v3 (level4_solar_qualify) salvava
-- in roofs.address solo locality + CAP della Google Solar API — spesso
-- il solo CAP (es. "80026"). L'indirizzo completo reale è stato
-- comunque catturato da Google Places e salvato su
-- subjects.sede_operativa_address.
--
-- Qui ricuciamo: per ogni roof il cui address è vuoto o un semplice CAP
-- di 5 cifre, lo sostituiamo con l'indirizzo completo del subject
-- collegato (quando disponibile). Il fix del funnel evita il problema
-- sui roof futuri; questa migration sistema quelli già creati.

UPDATE roofs r
SET address = s.sede_operativa_address,
    updated_at = now()
FROM leads l
JOIN subjects s ON s.id = l.subject_id
WHERE l.roof_id = r.id
  AND s.sede_operativa_address IS NOT NULL
  AND length(trim(s.sede_operativa_address)) > 0
  AND (r.address IS NULL OR r.address ~ '^\s*\d{5}\s*$');
