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
# move to ffmpeg-only rendering the only local heavy lifting is ffmpeg.
#
# Why system ffmpeg (not @ffmpeg-installer): that npm package ships a
# 2018 static build whose libavfilter predates the `xfade` filter
# (added in ffmpeg 4.3, 2020). The crossfade transition needs `xfade`,
# so we install Debian's ffmpeg (5.1.x on bookworm) and point the
# service at it via FFMPEG_PATH. The npm binary stays a local-dev
# fallback only.
# ──────────────────────────────────────────────────────────────────────────────

FROM node:22-bookworm-slim

# System deps:
#   ca-certificates: HTTPS to Replicate + Supabase
#   ffmpeg: video pipeline — crossfade `xfade` needs ffmpeg >= 4.3
#   fonts-dejavu-core: drawtext bold font (DejaVuSans-Bold renders € correctly)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    ffmpeg \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# ffmpeg-service.ts resolves its binary from FFMPEG_PATH first, then
# falls back to the bundled @ffmpeg-installer binary for local dev.
ENV FFMPEG_PATH=/usr/bin/ffmpeg

WORKDIR /app

# Pin pnpm: 10.x defaults `confirmModulesPurge=true` and exits non-zero on
# ignored build scripts in CI. `pnpm.onlyBuiltDependencies` in package.json
# whitelists ffmpeg + esbuild; pinning the version keeps the contract stable.
RUN npm install -g pnpm@10.26.1 --quiet

# Copy manifests first so Docker layer-caches the install step.
COPY package.json pnpm-lock.yaml* ./

# --frozen-lockfile when possible; --no-frozen as fallback during deps churn.
# Build scripts for the packages listed in package.json `pnpm.onlyBuiltDependencies`
# run automatically; everything else stays sandboxed.
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
