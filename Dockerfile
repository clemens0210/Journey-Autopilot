# Journey Autopilot — Python app image (src/ layout, editable install).
#
# STATUS: scaffold for milestone M0 (Scaffold / one-command setup). Not yet
# exercised end-to-end; see build spec §10/§11. The DB live-data sidecar
# (db_service/, Node) is a separate service — see docker-compose.yml.

FROM python:3.11-slim

WORKDIR /app

# System deps kept minimal; add build tools here if a wheel needs compiling.
COPY pyproject.toml requirements.txt ./
COPY src ./src

RUN pip install --no-cache-dir -e .

# App data (SQLite, Chroma) lives under the package data dir; mount a volume in
# compose to persist it across runs.
COPY config ./config
COPY data ./data
COPY scenarios ./scenarios
COPY scripts ./scripts
COPY run_onboarding.py ./

EXPOSE 8000

# Default: the onboarding/dashboard web app (which also hosts the trip chat).
# TODO(M0): a `docker compose up` health check that exercises the read path
# end-to-end (build spec §11).
CMD ["python", "run_onboarding.py"]
