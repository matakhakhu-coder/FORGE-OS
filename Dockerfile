# ════════════════════════════════════════════════════════════════════════
# FORGE — Containerized Runtime
# ════════════════════════════════════════════════════════════════════════
#
# Design decisions
# ────────────────
# • python:3.13-slim — matches the host Python version; no Node.js ever.
# • Non-root worker (forge:forge) — path-traversal exploits cannot escape
#   to /root or the host's C:\Users\matam path.
# • SQLite database lives on /data (named volume) — WAL mode works on
#   real filesystem volumes; do NOT mount as tmpfs.
# • media/ is a separate volume so artifact files persist across rebuilds.
# • FORGE_DB env var is respected by core/db/connection.py and all engines.
# • No pip install at runtime — dependencies are baked at build time and
#   the image is immutable.  Upgrade by rebuilding.
#
# Build:
#   docker compose build
#
# Run:
#   docker compose up -d
#
# Migrate existing DB into the container volume:
#   docker compose cp database.db forge:/data/database.db
#
# Run pipeline inside container:
#   docker compose exec forge python tools/mega_ingest.py
#
# ════════════════════════════════════════════════════════════════════════

FROM python:3.13-slim

# ── System dependencies ───────────────────────────────────────────────────────
# Required by: Pillow (libjpeg, zlib), lxml (libxml2/xslt), spaCy (gcc/g++),
#              pytesseract (tesseract-ocr), PyMuPDF (libmupdf deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
        libffi-dev \
        libssl-dev \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
        libjpeg-dev \
        libpng-dev \
        libwebp-dev \
        tesseract-ocr \
        tesseract-ocr-eng \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# ── Non-root user ─────────────────────────────────────────────────────────────
RUN groupadd -r forge && useradd -r -g forge -d /app forge

# ── Python dependencies ───────────────────────────────────────────────────────
WORKDIR /app

# Copy requirements first so Docker layer-caches the pip install step.
# A requirements.txt change triggers a full reinstall; app code changes do not.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download spaCy model (pinned to the version in requirements.txt)
RUN python -m spacy download en_core_web_sm

# ── Application code ──────────────────────────────────────────────────────────
COPY . .

# ── Volume mount points ───────────────────────────────────────────────────────
# /data  — SQLite database + WAL files (named volume, persisted)
# /app/media — uploaded artifacts (named volume, persisted)
# Both directories must be owned by the forge worker before privilege drop.
RUN mkdir -p /data /app/media \
    && chown -R forge:forge /data /app/media /app

# ── Environment ───────────────────────────────────────────────────────────────
ENV FORGE_DB=/data/database.db \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FLASK_ENV=production

# ── Drop privileges ───────────────────────────────────────────────────────────
# After this line the process cannot write outside /data or /app/media.
# A path-traversal RCE via Pillow/lxml cannot reach C:\Users\matam or /root.
USER forge

# ── Healthcheck ───────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/api/health', timeout=8)" \
    || exit 1

# ── Entrypoint ────────────────────────────────────────────────────────────────
EXPOSE 5000
CMD ["python", "app.py"]
