#!/usr/bin/env bash
# Availability probe for the Phase 9 failure drills: requests the status page
# once a second from this machine and, when stopped (Ctrl+C), prints how many
# requests failed and the longest outage. Turns "the page stayed up" into a number.
#
#   ./infra/probe.sh                       # https://mypatrolapp.cloud/
#   URL=https://example.org/ ./infra/probe.sh
#
# Each line: time, HTTP status (000 = no response), response time.
# A request counts as failed if it doesn't return 200 within 5 seconds.
set -uo pipefail

URL=${URL:-https://mypatrolapp.cloud/}
LOG=${LOG:-"probe-$(date +%Y%m%d-%H%M%S).log"}
total=0 failed=0 run=0 longest=0 longest_start="" run_start=""
started=$(date +%s)

summary() {
  local elapsed=$(( $(date +%s) - started ))
  echo
  echo "==> ${URL}: ${total} requests over ${elapsed}s, ${failed} failed"
  if (( total > 0 )); then
    awk -v t="$total" -v f="$failed" 'BEGIN { printf "    availability: %.2f%%\n", 100 * (t - f) / t }'
  fi
  if (( longest > 0 )); then
    echo "    longest outage: ${longest} consecutive failed requests (~${longest}s), starting ${longest_start}"
  else
    echo "    no failed requests"
  fi
  echo "    full log: ${LOG}"
  exit 0
}
trap summary INT TERM

echo "==> Probing ${URL} every second (Ctrl+C to stop and print the summary)"
while true; do
  now=$(date +%H:%M:%S)
  result=$(curl -s -o /dev/null --max-time 5 -w '%{http_code} %{time_total}' "$URL" 2>/dev/null || true)
  code=${result%% *}; secs=${result#* }
  [[ -z "$code" ]] && code=000 && secs=5
  total=$((total + 1))
  if [[ "$code" == "200" ]]; then
    if (( run > longest )); then longest=$run; longest_start=$run_start; fi
    run=0
    mark=""
  else
    failed=$((failed + 1))
    (( run == 0 )) && run_start=$now
    run=$((run + 1))
    mark="  <-- FAILED"
  fi
  if (( run > longest )); then longest=$run; longest_start=$run_start; fi
  printf '%s  %s  %5.2fs%s\n' "$now" "$code" "$secs" "$mark" | tee -a "$LOG"
  sleep 1
done
