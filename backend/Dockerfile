# syntax=docker/dockerfile:1
# Images from one Dockerfile (ADR 0005, ADR 0006, ADR 0009):
# - target `app` (default): api, worker, scheduler and migrations;
# - target `ml`: the internal embedding and text extraction service (ONNX Runtime, no
#   PyTorch; Tesseract OCR);
# - target `stt`: the internal speech-to-text service (faster-whisper, CPU), and
#   `stt-gpu`: the same with the CUDA libraries it needs (NVIDIA GPU, see compose.gpu.yaml).

FROM python:3.13-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

FROM build AS build-ml
RUN uv sync --locked --no-dev --no-editable --extra ml

FROM build AS build-stt
RUN uv sync --locked --no-dev --no-editable --extra stt

FROM build AS build-stt-gpu
RUN uv sync --locked --no-dev --no-editable --extra stt --extra stt-gpu

FROM python:3.13-slim AS runtime
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin lifeos
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

FROM runtime AS ml
# OCR (ADR 0008): Tesseract 5 with the French model only (no recommends: no English nor
# orientation data, no X11 libraries).
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-fra \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build-ml /app/.venv /app/.venv
# Owned by the service user: Docker copies this ownership into a new, empty named
# volume mounted here, so `ml-download` (uid 10001) can write the model.
RUN install -d -o lifeos -g lifeos -m 0755 /models
# The model is downloaded once into /models (`lifeos-ml download`); the service then
# runs offline. Override HF_HUB_OFFLINE=0 only for the download.
ENV HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 HF_HOME=/tmp/hf LIFEOS_EMBEDDINGS_CACHE_DIR=/models
USER lifeos
EXPOSE 8100
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=2)"]
CMD ["lifeos-ml"]

FROM runtime AS stt
COPY --from=build-stt /app/.venv /app/.venv
# Writable by the service user when Docker initialises a new, empty volume (`stt-download`).
RUN install -d -o lifeos -g lifeos -m 0755 /models
# The model is downloaded once into /models (`lifeos-stt download`); the service then
# runs offline and loads it on the first dictation.
ENV HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 HF_HOME=/tmp/hf LIFEOS_STT_CACHE_DIR=/models
USER lifeos
EXPOSE 8200
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8200/healthz', timeout=2)"]
CMD ["lifeos-stt"]

FROM runtime AS stt-gpu
COPY --from=build-stt-gpu /app/.venv /app/.venv
RUN install -d -o lifeos -g lifeos -m 0755 /models
# cuBLAS comes from its wheel; the NVIDIA Container Toolkit provides the driver library.
ENV HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 HF_HOME=/tmp/hf LIFEOS_STT_CACHE_DIR=/models \
    LIFEOS_STT_DEVICE=cuda \
    LD_LIBRARY_PATH=/app/.venv/lib/python3.13/site-packages/nvidia/cublas/lib
USER lifeos
EXPOSE 8200
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8200/healthz', timeout=2)"]
CMD ["lifeos-stt"]

FROM runtime AS app
COPY --from=build /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations ./migrations
# Encrypted document originals (named volume): owned by the service user so a new, empty
# volume mounted here is writable (same mechanism as /models in the `ml` target).
RUN install -d -o lifeos -g lifeos -m 0700 /data/documents
USER lifeos
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
# Only Traefik can reach the API (internal Docker networks), so forwarded headers are trusted.
CMD ["uvicorn", "lifeos.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--no-server-header"]
