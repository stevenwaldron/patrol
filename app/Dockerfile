# One Dockerfile, three images. Build from the REPO ROOT (it needs config/):
#   docker build -f app/Dockerfile --target worker      -t patrol/worker:TAG .
#   docker build -f app/Dockerfile --target scheduler   -t patrol/scheduler:TAG .
#   docker build -f app/Dockerfile --target status-page -t patrol/status-page:TAG .
#
# Base image comes from ECR Public's mirror of the official Python image, not
# Docker Hub: same image, but no Docker Hub anonymous pull limits (which shared
# CI runners hit), and it's served from AWS, close to the cluster.
#
# Pinned by DIGEST (its exact fingerprint), not just by name. The name
# "3.13-slim" is quietly repointed whenever Debian or Python ship updates, so the
# same commit could build a different image on different days, and the image tag
# (= commit ID) would stop meaning one exact build. Updating the base is now a
# deliberate one-line commit, which produces a new tag.
#   Pinned 2026-10-26: glibc 2.41-12+deb13u4, zlib 1.3.1-1+b1 (Debian 13 "trixie").
#   Find the current digest:
#     docker pull public.ecr.aws/docker/library/python:3.13-slim
#     docker image inspect --format '{{index .RepoDigests 0}}' public.ecr.aws/docker/library/python:3.13-slim
ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.13-slim@sha256:d98cc29d3266e535dd9da1099688d9cb3ca4753813571e071763d17725460962

# ---- Stage 1: install dependencies into a virtual environment ---------------
# Only requirements.txt is copied first, so this slow layer is cached and
# re-runs only when dependencies change, not on every code edit.
FROM ${PYTHON_IMAGE} AS deps
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY app/requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install -r /tmp/requirements.txt

# ---- Stage 2: shared runtime (no pip cache, no build leftovers, no tests) ---
FROM ${PYTHON_IMAGE} AS base
LABEL org.opencontainers.image.title="patrol" \
      org.opencontainers.image.description="Patrol uptime monitor" \
      org.opencontainers.image.licenses="MIT"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PROMETHEUS_DISABLE_CREATED_SERIES=True \
    PATROL_SITES_FILE=/app/config/sites.yaml
# A fixed, numeric, unprivileged user. Numeric matters: Kubernetes can only
# verify "runAsNonRoot" when the image's USER is a number, not a name.
RUN groupadd --system --gid 10001 patrol \
 && useradd --system --uid 10001 --gid patrol --no-create-home --shell /usr/sbin/nologin patrol
WORKDIR /app
COPY --from=deps /opt/venv /opt/venv
# Code and config stay owned by root: the app user can read them, not change them.
COPY app/patrol ./patrol
# Default site list; in the cluster a ConfigMap is mounted over this path.
COPY config/sites.yaml ./config/sites.yaml
USER 10001:10001

# ---- Final images -------------------------------------------------------------
FROM base AS scheduler
CMD ["python", "-m", "patrol.scheduler"]

FROM base AS worker
EXPOSE 9100
CMD ["python", "-m", "patrol.worker"]

FROM base AS status-page
EXPOSE 8080
# Access logs off: every request is already counted in Prometheus metrics.
CMD ["uvicorn", "patrol.status_page:create_app_from_env", "--factory", \
     "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
