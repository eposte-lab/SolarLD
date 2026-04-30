# ---- Builder ----
FROM python:3.12-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        libffi-dev \
        libjpeg-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md run.py ./
COPY src ./src
RUN pip install --upgrade pip && pip install .

# ---- Runtime ----
FROM python:3.12-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libjpeg62-turbo \
        zlib1g \
        curl \
        # WeasyPrint runtime deps — needed by the preventivo PDF renderer
        # (apps/api/src/services/quote_pdf_renderer.py). WeasyPrint links
        # against Pango (text shaping), Cairo (rasterization), GDK-Pixbuf
        # (raster image decode), HarfBuzz, and libffi at runtime.
        # `shared-mime-info` resolves <img src="..."> MIME types so the
        # render hero loads correctly. `fonts-dejavu` ships a sane
        # fallback font for Italian glyphs (€, à, ò, …) so the PDF
        # never falls back to .notdef tofu.
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        shared-mime-info \
        fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 solarlead
USER solarlead

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /app/src /app/src
COPY --from=builder /app/run.py /app/run.py

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["sh", "-c", "uvicorn src.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
