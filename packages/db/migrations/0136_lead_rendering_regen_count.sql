-- 0136_lead_rendering_regen_count.sql
--
-- Aggiunge leads.rendering_regen_count: quante volte il rendering di
-- quel lead è stato rigenerato manualmente dalla dashboard. Ogni
-- rigenerazione costa (Solar API + panel-paint nano-banana), quindi è
-- limitata a MAX_RENDERING_REGENERATIONS per lead (vedi
-- apps/api/src/routes/leads.py).

ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS rendering_regen_count INTEGER NOT NULL DEFAULT 0;
