-- ============================================================
-- 0012 — Supabase Storage buckets
-- ============================================================
-- Buckets for rendering images, videos, postcard PDFs, tenant logos.

-- Renderings (images/videos/gifs) — public read via slug
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
  'renderings',
  'renderings',
  true,
  52428800,  -- 50 MB
  ARRAY['image/png', 'image/jpeg', 'image/webp', 'image/gif', 'video/mp4']
)
ON CONFLICT (id) DO NOTHING;

-- Postcards (PDF for print provider)
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
  'postcards',
  'postcards',
  false,
  10485760,  -- 10 MB
  ARRAY['application/pdf']
)
ON CONFLICT (id) DO NOTHING;

-- Tenant branding (logos, etc.)
INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
  'branding',
  'branding',
  true,
  5242880,  -- 5 MB
  ARRAY['image/png', 'image/jpeg', 'image/svg+xml', 'image/webp']
)
ON CONFLICT (id) DO NOTHING;

-- Storage RLS policies

-- renderings: public read, authenticated tenant write for own tenant prefix
CREATE POLICY "Renderings public read" ON storage.objects
  FOR SELECT USING (bucket_id = 'renderings');

CREATE POLICY "Renderings tenant write" ON storage.objects
  FOR INSERT WITH CHECK (
    bucket_id = 'renderings'
    AND auth.role() = 'service_role'
  );

-- postcards: service role only
CREATE POLICY "Postcards service read" ON storage.objects
  FOR SELECT USING (
    bucket_id = 'postcards' AND auth.role() = 'service_role'
  );

CREATE POLICY "Postcards service write" ON storage.objects
  FOR INSERT WITH CHECK (
    bucket_id = 'postcards' AND auth.role() = 'service_role'
  );

-- branding: public read, tenant write (prefix = tenant_id)
CREATE POLICY "Branding public read" ON storage.objects
  FOR SELECT USING (bucket_id = 'branding');

CREATE POLICY "Branding tenant write" ON storage.objects
  FOR INSERT WITH CHECK (
    bucket_id = 'branding'
    AND (storage.foldername(name))[1] = auth_tenant_id()::text
  );
