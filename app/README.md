# Patrol services

Three Python services in one package, built into three container images.

| Service | Runs as (in Kubernetes) | What it does | Endpoints |
|---|---|---|---|
| `scheduler` | CronJob, every minute | Posts one patrol order per site to SQS, exits | none (health measured downstream) |
| `worker` | Deployment, scaled by KEDA on queue depth | Takes orders, checks sites, saves results to DynamoDB | `:9100/metrics`, `:9100/health` |
| `status_page` | Deployment behind a Service + Ingress | Status page, JSON API, site metrics | `/`, `/api/sites`, `/api/sites/{id}/history`, `/health`, `/ready`, `/metrics` |

## How an order flows

1. Scheduler reads `config/sites.yaml` and sends `{site_id, url, timeout_seconds, issued_at}` per site.
2. A worker long-polls SQS, checks the site (GET, follows redirects; 2xx/3xx = up), and saves
   `{site_id, checked_at, status, http_status, latency_ms, error, worker, expires_at}`.
3. Only after the save succeeds is the message deleted. If saving fails, SQS redelivers;
   after 3 failures the order goes to the dead-letter queue. Orders older than 2 minutes are skipped.
4. The status page reads 24h of history per site (cached 30s) for the page, the API and the metrics.

## Design notes

- **Down is a result, not an error.** A failed site check is saved and the order deleted.
- **Readiness doesn't check DynamoDB**: otherwise a DynamoDB blip would mark every copy
  not-ready at once and turn a dependency hiccup into a full outage.
- **The scheduler has no /metrics**: a one-second job can't be scraped. If it stops,
  `patrol_site_last_checked_age_seconds` climbs and alerts.
- **Graceful shutdown**: the worker finishes its current batch on SIGTERM (what Kubernetes sends).
- **Request metrics use route templates** (`/api/sites/{site_id}/history`), not raw URLs,
  so the number of metric series stays bounded; probes and scrapes are excluded from the SLO.

## Metrics

| Metric | From | Meaning |
|---|---|---|
| `patrol_checks_total{site,result}` | worker | checks completed, up/down |
| `patrol_check_duration_seconds{site}` | worker | time to check a site |
| `patrol_orders_skipped_total{reason}` | worker | stale or invalid orders |
| `patrol_worker_errors_total{stage}` | worker | receive / save / delete failures |
| `patrol_site_up{site}` | status page | 1 = latest check passed |
| `patrol_site_last_checked_age_seconds{site}` | status page | freshness (+Inf if no checks in 24h) |
| `patrol_site_uptime_ratio_24h{site}` | status page | share of passing checks |
| `patrol_http_requests_total{path,code}` | status page | SLO numerator/denominator |
| `patrol_http_request_duration_seconds{path}` | status page | SLO latency |

## Local development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest            # tests use moto + fake HTTP: no AWS, no internet
./run-local.sh scheduler              # against the real Patrol queue/table
./run-local.sh worker
./run-local.sh status-page            # http://localhost:8080
```
