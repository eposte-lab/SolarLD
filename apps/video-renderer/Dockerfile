# ──────────────────────────────────────────────────────────────────────────────
# SolarLead — video-renderer (Replicate + ffmpeg sidecar)
#
# Pipeline:
#   1. Replicate image-to-video model (Kling 1.6 by default) takes the
#      "before rooftop" frame + descriptive prompt → returns an MP4.
#   2. ffmpeg burns a stats overlay (kWp / savings / payback) on the
#      last ~1.5s and produces a Gmail-friendly GIF (~2-4 MB).
#   3. Both files uploaded to Supabase Storage; public URLs returned.
#
# Why no Chromium any more: the previous Remotion + Three.js path
# required a headless Chrome to render React compositions. With the
# move to a hosted model, the only local heavy lifting is ffmpeg —
# which we get bundled via @ffmpeg-installer/ffmpeg (no system binary
# needed). This shrinks the image from ~1.2 GB to ~300 MB and removes
# the X11 / chromium libstack from the runtime surface.
# ──────────────────────────────────────────────────────────────────────────────

FROM node:22-bookworm-slim

# Minimal system deps:
#   ca-certificates: HTTPS to Replicate + Supabase
#   fonts-dejavu-core: drawtext fallback (the bundled @ffmpeg-installer
#     ffmpeg picks the first system font when no fontfile is specified;
#     DejaVu Sans is the standard Linux default and renders € correctly)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install pnpm
RUN npm install -g pnpm@latest --quiet

# Copy manifests first so Docker layer-caches the install step.
COPY package.json pnpm-lock.yaml* ./

# --frozen-lockfile when possible; --no-frozen as fallback during deps
# churn. ignore-scripts is safe: @ffmpeg-installer's postinstall just
# unpacks a prebuilt binary into node_modules — but since we re-pull
# at install time the prebuilt is already on disk via npm tarball.
RUN pnpm install --frozen-lockfile 2>/dev/null || \
    pnpm install --no-frozen-lockfile

# Copy source (tsx reads TypeScript directly — no compile step needed)
COPY src/ ./src/
COPY tsconfig.json tsconfig.base.json* ../../tsconfig.base.json* ./

# ── Health-check ──────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD node -e "require('http').get('http://localhost:' + (process.env.PORT||4000) + '/health', r => process.exit(r.statusCode===200?0:1))"

EXPOSE 4000

# tsx runs TypeScript directly: no compile artifacts.
CMD ["npx", "tsx", "src/server.ts"]
