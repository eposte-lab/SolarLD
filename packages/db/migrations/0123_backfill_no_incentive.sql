-- 0123_backfill_no_incentive.sql
--
-- Allinea le derivations già persistite alla nuova policy "no auto-incentive":
-- l'utente vede l'investimento lordo (1000 €/kW B2B) senza alcuno sconto
-- automatico (Industria 4.0 / Superbonus si quotano in trattativa, non
-- in stima auto-generata). Vedi apps/api/src/services/roi_service.py.
--
-- Operazione: per ogni record con derivations/roi_data calcolato:
--   incentive_eur  → 0
--   net_capex_eur  → gross_capex_eur
--   payback_years  → gross_capex_eur / yearly_savings_eur (ricomputato)
--   roi_pct_25y    → ((savings_25y_eur - gross_capex_eur) / gross_capex_eur) * 100
--
-- Idempotente: usa COALESCE + filtra solo dove gross_capex_eur > 0.

BEGIN;

-- roofs.derivations
UPDATE roofs
SET derivations = derivations
    || jsonb_build_object(
        'incentive_eur', 0,
        'net_capex_eur', (derivations ->> 'gross_capex_eur')::numeric,
        'payback_years',
            CASE
                WHEN (derivations ->> 'yearly_savings_eur')::numeric > 0
                THEN round(
                    (derivations ->> 'gross_capex_eur')::numeric
                    / (derivations ->> 'yearly_savings_eur')::numeric,
                    1
                )
                ELSE NULL
            END,
        'roi_pct_25y',
            CASE
                WHEN (derivations ->> 'gross_capex_eur')::numeric > 0
                THEN round(
                    (
                        ((derivations ->> 'savings_25y_eur')::numeric
                         - (derivations ->> 'gross_capex_eur')::numeric)
                        / (derivations ->> 'gross_capex_eur')::numeric
                    ) * 100
                )
                ELSE 0
            END
    )
WHERE derivations IS NOT NULL
  AND derivations ? 'gross_capex_eur'
  AND (derivations ->> 'gross_capex_eur')::numeric > 0;

-- leads.roi_data (stessa shape — to_jsonb())
UPDATE leads
SET roi_data = roi_data
    || jsonb_build_object(
        'incentive_eur', 0,
        'net_capex_eur', (roi_data ->> 'gross_capex_eur')::numeric,
        'payback_years',
            CASE
                WHEN (roi_data ->> 'yearly_savings_eur')::numeric > 0
                THEN round(
                    (roi_data ->> 'gross_capex_eur')::numeric
                    / (roi_data ->> 'yearly_savings_eur')::numeric,
                    1
                )
                ELSE NULL
            END,
        'roi_pct_25y',
            CASE
                WHEN (roi_data ->> 'gross_capex_eur')::numeric > 0
                THEN round(
                    (
                        ((roi_data ->> 'savings_25y_eur')::numeric
                         - (roi_data ->> 'gross_capex_eur')::numeric)
                        / (roi_data ->> 'gross_capex_eur')::numeric
                    ) * 100
                )
                ELSE 0
            END
    )
WHERE roi_data IS NOT NULL
  AND roi_data ? 'gross_capex_eur'
  AND (roi_data ->> 'gross_capex_eur')::numeric > 0;

COMMIT;
