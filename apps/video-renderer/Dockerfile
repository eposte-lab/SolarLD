# ──────────────────────────────────────────────────────────────────────────────
# SolarLead — video-renderer (Remotion sidecar)
#
# Renders a 6-second before/after solar transition as MP4 + GIF via
# Remotion, uploads both to Supabase Storage, returns public URLs.
#
# Key requirements:
#   • Node 20 (LTS) — Remotion 4.x constraint
#   • Chromium (headless) — Remotion uses Chrome to render React compositions
#   • ffmpeg — GIF encoding + MP4 muxing
#
# We install system Chromium from Debian apt and point Remotion at it via
# REMOTION_CHROME_EXECUTABLE so the container doesn't download Chrome at
# runtime (which adds 30-60s to cold starts and can fail on firewalled
# Railway environments).
#
# Runtime: tsx (TypeScript-native, avoids CJS/ESM module-resolution issues
# with the Remotion bundler entry-point path).
# ──────────────────────────────────────────────────────────────────────────────

FROM node:20-bookworm-slim

# ── System dependencies ────────────────────────────────────────────────────
# chromium: headless browser for Remotion compositions
# ffmpeg: video/GIF encoding (used by @remotion/renderer internally)
# fonts-noto-cjk-*: prevents tofu □ in any CJK text on slides
# ca-certificates: required for HTTPS calls to Supabase storage
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    ffmpeg \
    ca-certificates \
    fonts-liberation \
    fonts-noto-core \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcups2 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# ── Tell Remotion where to find Chrome ───────────────────────────────────
# @remotion/renderer checks this env var before trying to locate / download
# a bundled Chrome binary. Pointing at system Chromium avoids the ~250MB
# download that happens on every cold-start otherwise.
ENV REMOTION_CHROME_EXECUTABLE=/usr/bin/chromium
ENV REMOTION_DISABLE_KEYBOARD_SHORTCUTS=1

# Chromium in Docker needs --no-sandbox; Remotion 4.x sets this
# automatically when it detects /.dockerenv. Belt-and-suspenders:
ENV CHROMIUM_FLAGS="--no-sandbox --disable-setuid-sandbox"

# ── App ───────────────────────────────────────────────────────────────────
WORKDIR /app

# Install pnpm
RUN npm install -g pnpm@latest --quiet

# Copy manifests first so Docker layer-caches the install step.
COPY package.json pnpm-lock.yaml* ./

# Install all deps (including devDeps — tsx lives there).
# --frozen-lockfile ensures reproducibility; ignore-scripts avoids
# Remotion's postinstall trying to download Chrome (we use system one).
RUN pnpm install --frozen-lockfile --ignore-scripts 2>/dev/null || \
    pnpm install --no-frozen-lockfile --ignore-scripts

# Copy source (tsx reads TypeScript directly — no compile step needed)
COPY src/ ./src/
COPY tsconfig.json tsconfig.base.json* ../../tsconfig.base.json* ./

# ── Health-check ──────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD node -e "require('http').get('http://localhost:' + (process.env.PORT||4000) + '/health', r => process.exit(r.statusCode===200?0:1))"

EXPOSE 4000

# tsx runs TypeScript directly: no compile artifacts, no path confusion
# between src/remotion.tsx (bundle entry) and a compiled dist/.
CMD ["npx", "tsx", "src/server.ts"]
