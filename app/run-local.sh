#!/usr/bin/env bash
# Run one Patrol service on your Mac against the REAL permanent layer
# (SQS queue + DynamoDB table in the Patrol account).
#
#   ./run-local.sh scheduler     # post one patrol order per site, then exit
#   ./run-local.sh worker        # check sites until Ctrl+C (metrics on :9100)
#   ./run-local.sh status-page   # web page on http://localhost:8080
#
# Settings are exported inside this script only, so nothing leaks into your shell.
set -euo pipefail
cd "$(dirname "$0")"

export AWS_PROFILE=patrol
export AWS_REGION=us-east-1
export PATROL_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/419701112481/patrol-checks
export PATROL_TABLE_NAME=patrol-checks
export PATROL_SITES_FILE=../config/sites.yaml
export PROMETHEUS_DISABLE_CREATED_SERIES=True  # drop the extra *_created timestamp series

if [[ ! -x .venv/bin/python ]]; then
  echo "No virtual environment yet. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt" >&2
  exit 1
fi

# The code needs Python 3.11+ (macOS's default python3 can be much older).
if ! .venv/bin/python -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "The .venv uses $(.venv/bin/python --version); Patrol needs 3.11 or newer." >&2
  echo "Rebuild it: rm -rf .venv && /usr/local/bin/python3.13 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt" >&2
  exit 1
fi

# Safety: refuse to run against any account but Patrol.
account=$(aws sts get-caller-identity --query Account --output text)
if [[ "$account" != "419701112481" ]]; then
  echo "Wrong AWS account ($account). Expected Patrol (419701112481)." >&2
  exit 1
fi

case "${1:-}" in
  scheduler)   exec .venv/bin/python -m patrol.scheduler ;;
  worker)      exec .venv/bin/python -m patrol.worker ;;
  status-page) exec .venv/bin/uvicorn patrol.status_page:create_app_from_env --factory --port 8080 ;;
  *) echo "Usage: $0 scheduler|worker|status-page" >&2; exit 2 ;;
esac
