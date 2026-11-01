# The read-only API (Hugging Face Spaces, Docker). The image holds code only: at
# startup the API downloads the newest results bundle from the public results
# dataset (PERIGEE_RESULTS_REPO), so a data refresh is a restart, not a rebuild
# (docs/decisions/0003-results-delivery.md).
#
# Build:  docker build -t perigee-api .
# Run:    docker run -p 7860:7860 -e PERIGEE_RESULTS_REPO=owner/perigee-results perigee-api

# Base image pinned by digest (both stages); Dependabot proposes updates.
FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337 AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements-api.txt /tmp/requirements-api.txt
# Every dependency ships a manylinux wheel: no compilers in either stage.
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --only-binary=:all: -r /tmp/requirements-api.txt

FROM python:3.12-slim-bookworm@sha256:e697c81897302a4c4333bade000d98038fbb21eb064f8ee1fd42d02ebcd9d337
# Spaces run containers as uid 1000.
RUN useradd --create-home --uid 1000 perigee
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY --chown=perigee:perigee src ./src
RUN mkdir -p /app/data/results && chown -R perigee:perigee /app/data
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PERIGEE_DATA_DIR=/app/data \
    PERIGEE_RESULTS_DIR=/app/data/results \
    PERIGEE_API_HOST=0.0.0.0 \
    PORT=7860 \
    SENTRY_ENVIRONMENT=production \
    # Behind the host's proxy: take the client address from X-Forwarded-For for the
    # per-IP rate limit (docs/threat-model.md notes that it can be spoofed).
    FORWARDED_ALLOW_IPS=*
USER perigee
EXPOSE 7860
HEALTHCHECK --interval=60s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/v1/health', timeout=4)"
CMD ["python", "-m", "src.main", "api"]
