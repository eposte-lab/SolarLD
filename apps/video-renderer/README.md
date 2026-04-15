# @solarlead/video-renderer

Remotion sidecar that renders before/after transition videos for the Creative Agent.

## Flow

1. FastAPI `CreativeAgent` uploads `before.jpg` + `after.jpg` to Supabase Storage.
2. It POSTs a render request to this sidecar: `POST /render`.
3. The sidecar uses `@remotion/renderer` to produce:
   - `transition.mp4` (1080×1080, 6s, 30fps)
   - `transition.gif` (480×480, 3s)
4. Assets are uploaded to Supabase Storage and public URLs are returned.

## Local Dev

```bash
pnpm install
pnpm dev        # starts express on :4000

# Render directly from CLI (preview):
pnpm render:example
```

## Environment

```
PORT=4000
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
```
