-- ============================================================
-- 0019 — events: realtime publication for future partitions
-- ============================================================
--
-- `events` is RANGE-partitioned monthly (migration 0008). The
-- `supabase_realtime` publication has `publish_via_partition_root =
-- false` and individual partitions (events_2026_04, ...) are already
-- registered. The original `ensure_events_partition()` helper creates
-- a partition but does NOT wire it into the publication, so the first
-- INSERT of next month would fall outside realtime.
--
-- This migration:
--   1. Rewrites `ensure_events_partition()` to also ADD the new
--      partition to `supabase_realtime` on creation (idempotent —
--      ADD TABLE on an already-published table is a no-op because we
--      guard with a pg_publication_tables lookup).
--   2. Bootstraps the publication for any existing partition that
--      may have been created outside the helper (e.g. manual SQL).
--
-- After this migration, dashboard realtime subscriptions on the
-- `events` table (via Supabase client filter `table: 'events'`)
-- receive INSERTs on every monthly partition — both existing and
-- future — without manual ops per month.

CREATE OR REPLACE FUNCTION ensure_events_partition(p_month DATE)
RETURNS VOID AS $$
DECLARE
  start_date DATE := date_trunc('month', p_month)::DATE;
  end_date   DATE := (date_trunc('month', p_month) + INTERVAL '1 month')::DATE;
  part_name  TEXT := 'events_' || to_char(start_date, 'YYYY_MM');
  already    BOOLEAN;
BEGIN
  -- Create the partition (idempotent).
  EXECUTE format(
    'CREATE TABLE IF NOT EXISTS %I PARTITION OF events
     FOR VALUES FROM (%L) TO (%L)',
    part_name, start_date, end_date
  );

  -- Register the partition with the realtime publication, unless
  -- already present. `ALTER PUBLICATION ... ADD TABLE` errors out if
  -- the table is already in the publication, so we check first.
  SELECT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime'
      AND schemaname = 'public'
      AND tablename = part_name
  ) INTO already;

  IF NOT already THEN
    EXECUTE format(
      'ALTER PUBLICATION supabase_realtime ADD TABLE public.%I',
      part_name
    );
  END IF;
END;
$$ LANGUAGE plpgsql;

-- Backfill: ensure all existing partitions are in the publication.
-- Safe because the function now guards for duplicates.
DO $$
DECLARE
  r RECORD;
BEGIN
  FOR r IN
    SELECT c.relname AS part_name
      FROM pg_inherits i
      JOIN pg_class c  ON c.oid = i.inhrelid
      JOIN pg_class p  ON p.oid = i.inhparent
     WHERE p.relname = 'events'
       AND c.relkind = 'r'
  LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_publication_tables
      WHERE pubname = 'supabase_realtime'
        AND schemaname = 'public'
        AND tablename = r.part_name
    ) THEN
      EXECUTE format(
        'ALTER PUBLICATION supabase_realtime ADD TABLE public.%I',
        r.part_name
      );
    END IF;
  END LOOP;
END;
$$;
