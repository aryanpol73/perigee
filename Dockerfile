# The read-only API as a container, for self-hosting (production itself is a static
# site with no server: docs/decisions/0003-deployment.md). The image holds code only:
# it serves the bundle in /app/data/results, or downloads the newest one at startup
# from a public results dataset (PERIGEE_RESULTS_REPO). CI builds and starts it.
#
# Build:  docker build -t perigee-api .
# Run:    docker run -p 7860:7860 -e PERIGEE_RESULTS_REPO=owner/perigee-results perigee-api

# Base image pinned by digest (both stages); Dependabot proposes updates.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements-api.txt /tmp/requirements-api.txt
# Every dependency ships a manylinux wheel: no compilers in either stage.
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --only-binary=:all: -r /tmp/requirements-api.txt

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df
# A fixed non-root user (uid 1000).
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
