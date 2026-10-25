#!/usr/bin/env bash
# Build all three Patrol images locally, for x86 (linux/amd64) to match the cluster nodes.
#
#   ./build-images.sh            # tag = current git commit, or "local" outside git
#
# Pushing to ECR is the CI pipeline's job (Phase 4), not this script's.
set -euo pipefail
cd "$(dirname "$0")/.."            # repo root: the build needs config/

tag=$(git rev-parse --short HEAD 2>/dev/null || echo local)
for service in scheduler worker status-page; do
  echo "==> building patrol/${service}:${tag}"
  docker build --platform linux/amd64 -f app/Dockerfile --target "$service" \
    -t "patrol/${service}:${tag}" -t "patrol/${service}:local" .
done

echo
docker image ls --format 'table {{.Repository}}\t{{.Tag}}\t{{.Size}}' | grep -E '^(REPOSITORY|patrol/)'
