# Journey Autopilot — app image (FastAPI web app + LLM agent + rights RAG).
#
# Build context: repo root. The DB live-data sidecar (db_service/, Node) is a
# separate image; docker-compose.yml wires both together:
#
#   cp .env.example .env    # fill in UNI_GPT_* so the trip chat works
#   docker compose up --build
#   -> http://127.0.0.1:8000

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# CPU-only torch first: as a plain dependency of sentence-transformers, pip
# would pull the default CUDA build (several GB of GPU libraries a container
# without GPU never uses). Installing the CPU wheel up front satisfies the
# requirement, so the next layer keeps it.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

# Dependencies as their own layer — code edits don't re-run pip.
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Bake the passenger-rights embedding model (~1.1 GB) into the image so the
# container never downloads it at runtime — rights_rag/rag_store.py loads with
# local_files_only first and finds it in the HF cache immediately.
RUN python -c "from sentence_transformers import SentenceTransformer; \
    SentenceTransformer('paraphrase-multilingual-mpnet-base-v2')"

# App code, editable install (deps already installed above).
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-deps -e .

COPY config ./config
COPY scenarios ./scenarios
COPY scripts ./scripts
COPY run_onboarding.py docker_entrypoint.py ./

# Inside the container the server must bind all interfaces (the published port
# never reaches a 127.0.0.1 bind). Runtime data (SQLite profile store + Chroma
# rights index) lives under /data — compose mounts a named volume there.
ENV ONBOARDING_HOST=0.0.0.0 \
    JA_DB_PATH=/data/journey_autopilot.db \
    CHROMA_PATH=/data/chromadb

EXPOSE 8000

# Entrypoint builds the rights index on first start (fresh /data volume),
# then execs the web app.
CMD ["python", "docker_entrypoint.py"]
