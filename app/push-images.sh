#!/usr/bin/env bash
# Push all three Patrol images to ECR, tagged with the current Git commit.
#
#   ./app/push-images.sh
#
# Rules it enforces:
#   - No uncommitted changes, so the tag always means "exactly this commit's code".
#   - Patrol account only.
#   - A tag already in ECR is skipped (tags are immutable: a re-push would fail).
# Builds any image that isn't built locally yet. Phase 4 moves this into GitHub Actions.
set -euo pipefail
cd "$(dirname "$0")/.."            # repo root

readonly ACCOUNT=419701112481 REGION=us-east-1 PROFILE=patrol
readonly REGISTRY="${ACCOUNT}.dkr.ecr.${REGION}.amazonaws.com"
readonly SERVICES=(scheduler worker status-page)

if [[ -n "$(git status --porcelain)" ]]; then
  echo "Uncommitted changes. Commit first, so the image tag matches the code in Git:" >&2
  git status --short >&2
  exit 1
fi
tag=$(git rev-parse --short=7 HEAD)

account=$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)
if [[ "$account" != "$ACCOUNT" ]]; then
  echo "Wrong AWS account ($account). Expected Patrol ($ACCOUNT)." >&2
  exit 1
fi

echo "==> logging in to ECR (token valid 12 hours, stored by Docker)"
aws ecr get-login-password --region "$REGION" --profile "$PROFILE" \
  | docker login --username AWS --password-stdin "$REGISTRY" >/dev/null

for service in "${SERVICES[@]}"; do
  repo="patrol/${service}"
  if aws ecr describe-images --region "$REGION" --profile "$PROFILE" \
       --repository-name "$repo" --image-ids imageTag="$tag" >/dev/null 2>&1; then
    echo "==> ${repo}:${tag} already in ECR, skipping"
    continue
  fi
  if ! docker image inspect "${repo}:${tag}" >/dev/null 2>&1; then
    echo "==> ${repo}:${tag} not built yet, building"
    docker build --platform linux/amd64 -f app/Dockerfile --target "$service" \
      -t "${repo}:${tag}" -t "${repo}:local" .
  fi
  echo "==> pushing ${repo}:${tag}"
  docker tag "${repo}:${tag}" "${REGISTRY}/${repo}:${tag}"
  docker push --quiet "${REGISTRY}/${repo}:${tag}"
done

echo
echo "==> ECR scan results (basic scan on push)"
for service in "${SERVICES[@]}"; do
  repo="patrol/${service}"
  if aws ecr wait image-scan-complete --region "$REGION" --profile "$PROFILE" \
       --repository-name "$repo" --image-id imageTag="$tag" 2>/dev/null; then
    counts=$(aws ecr describe-image-scan-findings --region "$REGION" --profile "$PROFILE" \
      --repository-name "$repo" --image-id imageTag="$tag" \
      --query 'imageScanFindings.findingSeverityCounts' --output json)
    echo "    ${repo}:${tag}  ${counts//[$'\n ']/}"
  else
    echo "    ${repo}:${tag}  scan not available (see the ECR console)"
  fi
done

echo
echo "Done. To deploy this build, set in deploy/charts/patrol/values.yaml:"
echo "    tag: \"${tag}\""
echo "then commit and push; Argo CD rolls it out."
