#!/usr/bin/env bash
# Run one Patrol image locally against the real Patrol queue/table.
#
#   ./run-container.sh worker
#   ./run-container.sh status-page      # http://localhost:8080
#   ./run-container.sh scheduler
#
# Credentials: the container gets SHORT-LIVED role credentials (about 1 hour),
# exported inside a subshell so they never land in your terminal session.
# Nothing from ~/.aws is copied or mounted into the container. (In the cluster,
# EKS Pod Identity hands pods short-lived credentials the same way.)
set -euo pipefail
service="${1:-}"
case "$service" in
  scheduler)   ports=() ;;
  worker)      ports=(-p 9100:9100) ;;
  status-page) ports=(-p 8080:8080) ;;
  *) echo "Usage: $0 scheduler|worker|status-page" >&2; exit 2 ;;
esac

account=$(aws sts get-caller-identity --profile patrol --query Account --output text)
if [[ "$account" != "419701112481" ]]; then
  echo "Wrong AWS account ($account). Expected Patrol (419701112481)." >&2
  exit 1
fi

(
  eval "$(aws configure export-credentials --profile patrol --format env)"
  exec docker run --rm --init --name "patrol-${service}" ${ports[@]+"${ports[@]}"} \
    -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
    -e AWS_REGION=us-east-1 \
    -e PATROL_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/419701112481/patrol-checks \
    -e PATROL_TABLE_NAME=patrol-checks \
    "patrol/${service}:local"
)
