#!/usr/bin/env bash
# Load test for the worker autoscaler (Phase 7): post a burst of fake patrol
# orders to the SQS queue and watch KEDA scale the workers out, then back in.
#
#   ./infra/load-test.sh                 # default: 30 waves of 100 orders (~10 minutes)
#   WAVES=12 PER_WAVE=60 ./infra/load-test.sh
#
# The orders point at 192.0.2.1, an address reserved for documentation
# (RFC 5737) that is never routed. Each check simply waits for its 5-second
# timeout, so every order is slow, real work for a worker, and no actual website
# receives any traffic. Results are stored under site "load-test": the status
# page never shows it (it lists only configured sites) and DynamoDB's TTL
# removes the rows on its own.
#
# Throughput: a worker runs 10 checks at a time, 5 s each = about 2 orders/s.
# 100 orders per 10 s needs about 5 workers just to keep up, so the queue grows
# until KEDA has scaled out (target 20 orders per worker, max 10 workers).
set -euo pipefail

PROFILE=patrol
EXPECTED_ACCOUNT=419701112481
REGION=us-east-1
QUEUE_NAME=patrol-checks
WAVES=${WAVES:-30}             # 30 waves; each = ~11 s of API calls + INTERVAL
PER_WAVE=${PER_WAVE:-100}      # orders per wave, sent 10 per API call
INTERVAL=${INTERVAL:-10}       # seconds between waves
# Never answers (documentation-only address). Assumption to verify live: AWS
# drops traffic to it silently, so each check waits the full timeout. If the
# dashboard's "Check duration" for load-test shows ~0 s instead of ~5 s, the
# network is refusing fast; then use an unassigned address inside the VPC,
# which the VPC always drops silently:  TARGET_URL=http://10.20.255.254/
TARGET_URL=${TARGET_URL:-"http://192.0.2.1/"}

account=$(aws sts get-caller-identity --profile "$PROFILE" --query Account --output text)
if [[ "$account" != "$EXPECTED_ACCOUNT" ]]; then
  echo "Wrong AWS account ($account). Expected Patrol ($EXPECTED_ACCOUNT)." >&2
  exit 1
fi
queue_url=$(aws sqs get-queue-url --profile "$PROFILE" --region "$REGION" \
  --queue-name "$QUEUE_NAME" --query QueueUrl --output text)

queue_depth() {
  aws sqs get-queue-attributes --profile "$PROFILE" --region "$REGION" --queue-url "$queue_url" \
    --attribute-names ApproximateNumberOfMessages ApproximateNumberOfMessagesNotVisible \
    --query '[Attributes.ApproximateNumberOfMessages, Attributes.ApproximateNumberOfMessagesNotVisible]' \
    --output text | awk '{print "waiting=" $1 " in-progress=" $2}'
}

echo "==> Sending $((WAVES * PER_WAVE)) orders: $PER_WAVE every ${INTERVAL}s, $WAVES waves"
echo "    Watch in another tab:  kubectl --context patrol -n patrol get hpa,deploy/worker -w"
for wave in $(seq 1 "$WAVES"); do
  # Same format as the scheduler's orders (app/patrol/scheduler.py). macOS
  # `date` has no sub-second format, so the fraction is fixed at zero.
  issued_at=$(date -u +%Y-%m-%dT%H:%M:%S.000000Z)
  body="{\"site_id\":\"load-test\",\"url\":\"${TARGET_URL}\",\"timeout_seconds\":5,\"issued_at\":\"${issued_at}\"}"
  body_json=${body//\"/\\\"}
  for ((sent = 0; sent < PER_WAVE; sent += 10)); do
    entries="["
    for i in $(seq 0 9); do
      entries+="{\"Id\":\"$i\",\"MessageBody\":\"${body_json}\"}"
      [[ $i -lt 9 ]] && entries+=","
    done
    entries+="]"
    # shellcheck disable=SC2016  # backticks are a JMESPath literal, not a shell command
    failed=$(aws sqs send-message-batch --profile "$PROFILE" --region "$REGION" \
      --queue-url "$queue_url" --entries "$entries" --query 'length(Failed || `[]`)' --output text)
    [[ "$failed" == "0" ]] || echo "    warning: $failed orders in this batch were not accepted" >&2
  done
  printf '    wave %2d/%d sent   queue: %s\n' "$wave" "$WAVES" "$(queue_depth)"
  [[ $wave -lt $WAVES ]] && sleep "$INTERVAL"
done

echo "==> Done sending. The queue drains, then KEDA removes workers after 2 quiet minutes."
for _ in $(seq 1 12); do
  printf '    queue: %s\n' "$(queue_depth)"
  sleep 15
done
