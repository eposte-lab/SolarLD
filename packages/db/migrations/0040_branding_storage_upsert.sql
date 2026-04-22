-- 0040 — Allow tenants to UPDATE and DELETE their own branding assets.
--
-- 0012 created the `branding` bucket + an INSERT policy scoped to
-- `(storage.foldername(name))[1] = auth_tenant_id()::text`. That works
-- for first-time uploads but `upsert:true` in the JS client issues a
-- PUT that the storage layer rejects with 403 because there's no
-- matching UPDATE policy. Replacing the logo therefore required the
-- user to delete+reinsert via service-role, which the dashboard can't
-- do.
--
-- This migration adds UPDATE and DELETE policies with the same
-- tenant-prefix constraint, so:
--   - `supabase.storage.from('branding').upload(path, file, {upsert:true})`
--     works from the browser with the user's JWT.
--   - A future "Remove logo" UI can clear the object directly.
--
-- Public read (from 0012) stays as-is — logo URLs embedded in emails
-- are fetched by the recipient's mail client without any auth.

BEGIN;

DROP POLICY IF EXISTS "Branding tenant update" ON storage.objects;
CREATE POLICY "Branding tenant update" ON storage.objects
    FOR UPDATE
    USING (
        bucket_id = 'branding'
        AND (storage.foldername(name))[1] = auth_tenant_id()::text
    )
    WITH CHECK (
        bucket_id = 'branding'
        AND (storage.foldername(name))[1] = auth_tenant_id()::text
    );

DROP POLICY IF EXISTS "Branding tenant delete" ON storage.objects;
CREATE POLICY "Branding tenant delete" ON storage.objects
    FOR DELETE
    USING (
        bucket_id = 'branding'
        AND (storage.foldername(name))[1] = auth_tenant_id()::text
    );

COMMIT;
