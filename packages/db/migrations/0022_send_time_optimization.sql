-- ============================================================
-- 0022 — smart send-time optimization (Part B.3)
-- ============================================================
--
-- Stores a per-lead "best UTC hour to send follow-ups" derived
-- nightly from the lead's own email-open history. The follow-up
-- cron reads this column and defers the arq job to that hour
-- instead of sending immediately — atteso +20-30% open-rate sui
-- nudge di step-2/3, coerente con la letteratura (Campaign Monitor,
-- Mailchimp 2023: aperture concentrate nel 3-5h post-invio, quindi
-- allineare invio al picco individuale sposta il vertice della
-- gaussiana).
--
-- Null = nessun segnale ancora (lead nuovo o sempre silente): il
-- servizio torna al default tenant (``tenants.settings.default_send_hour_utc``)
-- o, in assenza, all'ora globale ``DEFAULT_SEND_HOUR_UTC`` (09:00 UTC
-- ≈ 10-11 CET, fascia business plausibile per l'ATECO italiano
-- standard).
--
-- Unità: UTC (non locale). Motivazione: tutti i tenant sono al
-- momento italiani (CET/CEST) e il jitter DST è < 1h, dentro al
-- ±30min di tolleranza della logica in ``send_time_service``.
-- Quando apriremo a paesi oltre CET useremo ``tenants.timezone``
-- (già esistente nello schema) per convertire; fino ad allora è
-- over-engineering.

ALTER TABLE leads
  ADD COLUMN IF NOT EXISTS best_send_hour SMALLINT
    CHECK (best_send_hour IS NULL OR best_send_hour BETWEEN 0 AND 23);

COMMENT ON COLUMN leads.best_send_hour IS
  'UTC hour-of-day (0-23) at which this lead historically opens '
  'emails. Refreshed nightly by send_time_rollup_cron from the '
  'last 180d of events(lead.email_opened | lead.email_clicked). '
  'NULL = no signal yet (fall back to tenant default / 09 UTC). '
  'See apps/api/src/services/send_time_service.py.';

-- No index: read-by-lead-id only (PK), and the column is consumed
-- by the follow-up cron one lead at a time. Partial indexes on
-- SMALLINT flags tend to hurt INSERTs more than they help reads.
