"""Status page: HTML page, JSON API and Prometheus metrics, all read from DynamoDB.

Endpoints
  /                          the public status page
  /api/sites                 summary of every site (JSON)
  /api/sites/{id}/history    recent checks for one site (JSON)
  /health                    liveness: the process is running
  /ready                     readiness: config loaded, able to serve
  /metrics                   Prometheus metrics

Readiness deliberately does NOT check DynamoDB. If it did, a short DynamoDB
blip would mark every copy "not ready" at once and the Service would have
nowhere to send visitors: a dependency hiccup would become a full outage.

Run locally:  uvicorn patrol.status_page:create_app_from_env --factory
"""

import html
import math
import threading
import time
from datetime import datetime, timedelta, timezone

import boto3
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from .config import Settings
from .logs import setup_logging
from .sites import Site, load_sites
from .store import ResultStore, from_iso

# -- metrics ------------------------------------------------------------------
# Request metrics feed the SLO ("99% of page loads succeed within 0.5s").
REQUESTS = Counter("patrol_http_requests_total", "HTTP requests served", ["path", "code"])
REQUEST_SECONDS = Histogram(
    "patrol_http_request_duration_seconds", "HTTP request latency", ["path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)
# Site metrics are the product: what the monitor has found.
SITE_UP = Gauge("patrol_site_up", "1 if the latest check passed, 0 if it failed", ["site"])
SITE_AGE = Gauge("patrol_site_last_checked_age_seconds",
                 "Seconds since the latest check (+Inf if never checked in 24h)", ["site"])
SITE_LATENCY = Gauge("patrol_site_latency_ms", "Latency of the latest check", ["site"])
SITE_UPTIME = Gauge("patrol_site_uptime_ratio_24h", "Share of checks passing over 24h", ["site"])

UNTRACKED_PATHS = {"/metrics", "/health", "/ready"}  # probes and scrapes don't count toward the SLO


class SummaryCache:
    """Recompute the site summary at most every N seconds.

    Page loads and Prometheus scrapes both read the summary; without a cache,
    every one of them would query DynamoDB for 24h of history per site.
    """

    def __init__(self, compute, ttl_seconds: int):
        self.compute, self.ttl = compute, ttl_seconds
        self.value, self.expires, self.lock = None, 0.0, threading.Lock()

    def get(self):
        with self.lock:
            if self.value is None or time.monotonic() >= self.expires:
                self.value = self.compute()
                self.expires = time.monotonic() + self.ttl
            return self.value


def summarize_site(site: Site, checks: list[dict], now: datetime, stale_after: int) -> dict:
    """Turn 24h of checks for one site into what the page and metrics need."""
    summary = {
        "id": site.id, "name": site.name, "url": site.url,
        "status": "unknown", "last_checked_at": None, "age_seconds": None,
        "latency_ms": None, "http_status": None, "error": None,
        "checks_24h": len(checks), "uptime_24h_percent": None,
        "recent": [c["status"] for c in checks[-40:]],
    }
    if not checks:
        return summary
    latest = checks[-1]
    age = (now - from_iso(latest["checked_at"])).total_seconds()
    up_count = sum(1 for c in checks if c["status"] == "up")
    summary.update(
        status=("stale" if age > stale_after else latest["status"]),
        last_checked_at=latest["checked_at"], age_seconds=round(age),
        latency_ms=latest.get("latency_ms"), http_status=latest.get("http_status"),
        error=latest.get("error"),
        uptime_24h_percent=round(100 * up_count / len(checks), 2),
    )
    return summary


def create_app(store: ResultStore, sites: list[Site], *, cache_seconds: int = 30,
               stale_after: int = 300) -> FastAPI:
    app = FastAPI(title="Patrol status page", docs_url=None, redoc_url=None)
    sites_by_id = {s.id: s for s in sites}

    def compute_summary() -> list[dict]:
        now = datetime.now(timezone.utc)
        since = now - timedelta(hours=24)
        result = []
        for site in sites:
            s = summarize_site(site, store.history(site.id, since), now, stale_after)
            result.append(s)
            # Update the site gauges from the same data the page shows.
            if s["status"] in ("up", "down"):
                SITE_UP.labels(site.id).set(1 if s["status"] == "up" else 0)
            SITE_AGE.labels(site.id).set(s["age_seconds"] if s["age_seconds"] is not None else math.inf)
            if s["latency_ms"] is not None:
                SITE_LATENCY.labels(site.id).set(s["latency_ms"])
            if s["uptime_24h_percent"] is not None:
                SITE_UPTIME.labels(site.id).set(s["uptime_24h_percent"] / 100)
        return result

    cache = SummaryCache(compute_summary, cache_seconds)

    @app.middleware("http")
    async def record_request(request: Request, call_next):
        start = time.perf_counter()
        code = 500
        try:
            response = await call_next(request)
            code = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            path = route.path if route else "unmatched"  # template path keeps label count bounded
            if path not in UNTRACKED_PATHS:
                REQUESTS.labels(path, str(code)).inc()
                REQUEST_SECONDS.labels(path).observe(time.perf_counter() - start)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        return {"status": "ready", "sites": len(sites)}

    @app.get("/metrics")
    def metrics():
        cache.get()  # refresh site gauges (cached, so scrapes stay cheap)
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/api/sites")
    def api_sites():
        return {"generated_at": datetime.now(timezone.utc).isoformat(), "sites": cache.get()}

    @app.get("/api/sites/{site_id}/history")
    def api_history(site_id: str, hours: int = Query(24, ge=1, le=168)):
        if site_id not in sites_by_id:
            raise HTTPException(status_code=404, detail="unknown site")
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        return {"site_id": site_id, "hours": hours, "checks": store.history(site_id, since)}

    @app.get("/", response_class=HTMLResponse)
    def page():
        return render_page(cache.get())

    return app


# -- HTML -----------------------------------------------------------------------
_LABELS = {"up": "Operational", "down": "Down", "stale": "No recent data", "unknown": "Not checked yet"}


def _fmt_age(seconds) -> str:
    if seconds is None:
        return "never"
    if seconds < 90:
        return f"{seconds}s ago"
    if seconds < 5400:
        return f"{round(seconds / 60)} min ago"
    return f"{round(seconds / 3600)} h ago"


def render_page(summary: list[dict]) -> str:
    e = html.escape
    all_up = summary and all(s["status"] == "up" for s in summary)
    banner = "All systems operational" if all_up else "Some systems need attention"
    rows = []
    for s in summary:
        bars = "".join(f'<i class="b {e(st)}"></i>' for st in s["recent"]) or '<span class="muted">no data</span>'
        uptime = f'{s["uptime_24h_percent"]:.2f}%' if s["uptime_24h_percent"] is not None else "—"
        # Only show latency if the site actually answered; time-to-failure isn't latency.
        answered = s["latency_ms"] is not None and s["http_status"] is not None
        latency = f'{s["latency_ms"]} ms' if answered else "—"
        detail = f'<div class="err">{e(s["error"])}</div>' if s["error"] and s["status"] != "up" else ""
        rows.append(f"""
      <section class="site">
        <div class="head">
          <div><h2>{e(s["name"])}</h2><a href="{e(s["url"])}" rel="noopener">{e(s["url"])}</a></div>
          <span class="pill {e(s["status"])}">{_LABELS.get(s["status"], e(s["status"]))}</span>
        </div>
        <div class="bars" aria-label="Recent checks, oldest to newest">{bars}</div>
        <dl>
          <div><dt>Uptime (24h)</dt><dd>{uptime}</dd></div>
          <div><dt>Latency</dt><dd>{latency}</dd></div>
          <div><dt>Last checked</dt><dd>{_fmt_age(s["age_seconds"])}</dd></div>
          <div><dt>Checks (24h)</dt><dd>{s["checks_24h"]}</dd></div>
        </dl>{detail}
      </section>""")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Patrol status</title>
<style>
  :root {{ --bg:#f6f7f9; --card:#fff; --text:#1b1f24; --muted:#6a737d; --line:#e3e6ea;
          --up:#1f9d55; --down:#d1364a; --stale:#c08a1e; --unknown:#9aa1a9; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0f1216; --card:#171b21; --text:#e6e8eb; --muted:#9aa3ad; --line:#262c34; }} }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
         font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif; }}
  main {{ max-width:760px; margin:0 auto; padding:32px 16px 48px; }}
  h1 {{ font-size:22px; margin:0 0 4px; letter-spacing:-.01em; }}
  .banner {{ color:var(--muted); margin:0 0 24px; }}
  .site {{ background:var(--card); border:1px solid var(--line); border-radius:12px;
          padding:18px; margin-bottom:14px; }}
  .head {{ display:flex; justify-content:space-between; gap:12px; align-items:flex-start; }}
  h2 {{ font-size:16px; margin:0; }}
  .head a {{ color:var(--muted); font-size:13px; word-break:break-all; }}
  .pill {{ font-size:12px; font-weight:600; padding:3px 10px; border-radius:999px;
          white-space:nowrap; color:#fff; background:var(--unknown); }}
  .pill.up {{ background:var(--up); }} .pill.down {{ background:var(--down); }}
  .pill.stale {{ background:var(--stale); }}
  .bars {{ display:flex; gap:2px; margin:14px 0 12px; height:26px; align-items:stretch; }}
  .b {{ flex:1; max-width:12px; border-radius:2px; background:var(--unknown); }}
  .b.up {{ background:var(--up); }} .b.down {{ background:var(--down); }}
  dl {{ display:grid; grid-template-columns:repeat(4,1fr); gap:8px; margin:0; }}
  dt {{ color:var(--muted); font-size:12px; }} dd {{ margin:0; font-weight:600; }}
  .err {{ margin-top:10px; font-size:13px; color:var(--down); }}
  .muted {{ color:var(--muted); font-size:13px; }}
  footer {{ color:var(--muted); font-size:12px; margin-top:24px; }}
  @media (max-width:520px) {{ dl {{ grid-template-columns:repeat(2,1fr); }} }}
</style></head>
<body><main>
  <h1>Patrol</h1>
  <p class="banner">{banner}</p>{"".join(rows)}
  <footer>Checked every minute from a Kubernetes cluster on AWS. Refreshes every 60 seconds.</footer>
</main></body></html>"""


def create_app_from_env() -> FastAPI:
    setup_logging("status-page")
    settings = Settings.from_env()
    settings.require("table_name")
    table = boto3.resource("dynamodb", region_name=settings.region).Table(settings.table_name)
    return create_app(
        ResultStore(table, settings.result_retention_days),
        load_sites(settings.sites_file),
        cache_seconds=settings.summary_cache_seconds,
        stale_after=settings.stale_after_seconds,
    )
