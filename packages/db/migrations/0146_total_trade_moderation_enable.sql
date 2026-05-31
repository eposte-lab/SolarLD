-- ============================================================
-- 0146 — Enable trial moderation for Total Trade
-- ============================================================
-- Activates the moderation gate built in 0145 for the single trial
-- tenant. Kept as a separate, trivially-revertible step so 0145
-- (the risky RLS DDL) can ship and be verified behavior-neutral
-- first. To roll back: remove the trial_moderation key (or set it to
-- anything other than 'true') from settings.feature_flags.
--
-- The flag lives in tenants.settings.feature_flags — the same map the
-- PATCH /v1/admin/tenants/{id}/feature-flags endpoint manages:
--   trial_moderation = true → leads hidden until released, inbound
--                             appointment requests held for approval.
--
-- tenant_is_moderated() (0145) reads it with `->>` (text extraction),
-- so the JSON string "true" and the boolean true are both accepted.
--
-- Robust, idempotent merge: feature_flags may not exist on the row yet,
-- and jsonb_set() will NOT create a missing parent key. So we build /
-- preserve feature_flags with `||` instead, which both creates it when
-- absent and keeps any other flags already present. Only trial_moderation
-- is written here — the operator-email routing was removed (the
-- super-admin inbound queue is the single review surface).
-- ============================================================

UPDATE tenants
SET settings = COALESCE(settings, '{}'::jsonb)
  || jsonb_build_object(
       'feature_flags',
       COALESCE(settings->'feature_flags', '{}'::jsonb)
         || '{"trial_moderation":"true"}'::jsonb
     ),
    updated_at = now()
WHERE id = 'df08df04-4c90-4613-b21e-80879fc958d1';
