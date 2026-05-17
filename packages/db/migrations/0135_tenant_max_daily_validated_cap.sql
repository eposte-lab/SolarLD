-- 0135_tenant_max_daily_validated_cap.sql
--
-- Aggiunge tenants.max_daily_validated_cap: il tetto di "lead validati
-- / giorno" consentito dal piano tariffario del tenant. Il creatore di
-- scansione (dashboard) limita il campo a questo valore e l'API
-- /v1/scan-jobs lo rifiuta se superato. NULL = nessun limite di piano
-- (resta solo il tetto tecnico assoluto di 5000).

ALTER TABLE tenants
  ADD COLUMN IF NOT EXISTS max_daily_validated_cap INTEGER;

-- Total Trade è in trial: massimo 20 lead validati al giorno.
UPDATE tenants
SET max_daily_validated_cap = 20,
    updated_at = now()
WHERE id = 'df08df04-4c90-4613-b21e-80879fc958d1';
