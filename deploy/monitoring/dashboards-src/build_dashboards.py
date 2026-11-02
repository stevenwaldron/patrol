"""Generate Patrol's Grafana dashboards (JSON) from one readable definition.

    python3 deploy/monitoring/dashboards-src/build_dashboards.py

Writes deploy/charts/patrol/files/dashboards/*.json, which the Patrol chart ships
as ConfigMaps that Grafana's sidecar loads. Edit THIS file, not the JSON; CI
checks the JSON is up to date.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[2] / "charts/patrol/files/dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}


def panel(pid, title, kind, x, y, w, h, targets, unit=None, desc="", **extra):
    p = {
        "id": pid, "title": title, "type": kind, "datasource": DS, "description": desc,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [{"refId": chr(65 + i), "datasource": DS, "expr": e, "legendFormat": l}
                    for i, (e, l) in enumerate(targets)],
        "fieldConfig": {"defaults": {}, "overrides": []},
        "options": {},
    }
    if unit:
        p["fieldConfig"]["defaults"]["unit"] = unit
    for k, v in extra.items():
        if k == "defaults":
            p["fieldConfig"]["defaults"].update(v)
        else:
            p[k] = v
    return p


def dashboard(uid, title, desc, panels):
    return {
        "uid": uid, "title": title, "description": desc, "tags": ["patrol"],
        "timezone": "browser", "schemaVersion": 39, "version": 1, "editable": True,
        "refresh": "30s", "time": {"from": "now-3h", "to": "now"},
        "templating": {"list": []}, "annotations": {"list": []}, "panels": panels,
    }


UP_MAPPING = {"mappings": [{"type": "value", "options": {
    "0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}],
    "color": {"mode": "thresholds"},
    "thresholds": {"mode": "absolute", "steps": [{"color": "red", "value": None},
                                                  {"color": "green", "value": 1}]}}

patrol = dashboard(
    "patrol-overview", "Patrol: overview",
    "What Patrol sees (monitored sites) and how Patrol itself is doing.",
    [
        panel(1, "Site status (latest check)", "stat", 0, 0, 12, 5,
              [('min by (site) (patrol_site_up)', "{{site}}")],
              desc="UP/DOWN per monitored site, as shown on the status page. broken-test is down on purpose.",
              defaults=UP_MAPPING, options={"colorMode": "background", "graphMode": "none",
                                            "textMode": "value_and_name",
                                            "reduceOptions": {"calcs": ["lastNotNull"]}}),
        panel(2, "Uptime, last 24h", "stat", 12, 0, 12, 5,
              [('min by (site) (patrol_site_uptime_ratio_24h)', "{{site}}")], unit="percentunit",
              defaults={"decimals": 2},
              options={"textMode": "value_and_name", "reduceOptions": {"calcs": ["lastNotNull"]}}),
        panel(3, "Site latency (latest check)", "timeseries", 0, 5, 12, 8,
              [('min by (site) (patrol_site_latency_ms)', "{{site}}")], unit="ms"),
        panel(4, "Check duration, p95 (worker)", "timeseries", 12, 5, 12, 8,
              [('histogram_quantile(0.95, sum by (site, le) (rate(patrol_check_duration_seconds_bucket[5m])))', "{{site}}")],
              unit="s"),
        panel(5, "Checks per minute, by result", "timeseries", 0, 13, 12, 8,
              [('sum by (result) (rate(patrol_checks_total[5m])) * 60', "{{result}}")], unit="short"),
        panel(6, "Age of latest check (freshness SLO: < 3 min)", "timeseries", 12, 13, 12, 8,
              [('patrol:site_check_age_seconds:min', "{{site}}")], unit="s",
              defaults={"thresholds": {"mode": "absolute", "steps": [
                  {"color": "green", "value": None}, {"color": "red", "value": 180}]},
                  "custom": {"thresholdsStyle": {"mode": "line"}}}),
        panel(7, "Worker errors and skipped orders", "timeseries", 0, 21, 12, 8,
              [('sum by (stage) (rate(patrol_worker_errors_total[5m])) * 60', "error: {{stage}}"),
               ('sum by (reason) (rate(patrol_orders_skipped_total[5m])) * 60', "skipped: {{reason}}")],
              unit="short", desc="Per minute. Should be flat at zero."),
        panel(8, "Status page: requests per second, by code", "timeseries", 12, 21, 12, 8,
              [('sum by (code) (rate(patrol_http_requests_total[5m]))', "{{code}}")], unit="reqps",
              desc="Real visitor requests only (probes and /metrics excluded)."),
    ])

slo = dashboard(
    "patrol-slo", "Patrol: SLOs and error budgets",
    "Two SLOs, their error budgets and burn rates (multi-window alerts fire at 14.4x and 6x).",
    [
        panel(1, "Availability SLI, last 24h (target 99.5%)", "stat", 0, 0, 6, 5,
              [('1 - (sum(increase(patrol_http_requests_total{code=~"5.."}[24h])) or vector(0)) / sum(increase(patrol_http_requests_total[24h]))', "")],
              unit="percentunit", defaults={"decimals": 3, "thresholds": {"mode": "absolute", "steps": [
                  {"color": "red", "value": None}, {"color": "green", "value": 0.995}]}},
              desc="Share of real status-page requests without a 5xx. Metrics are kept 24h (they go with the cluster at teardown)."),
        panel(2, "Availability error budget left (24h)", "gauge", 6, 0, 6, 5,
              [('1 - ((sum(increase(patrol_http_requests_total{code=~"5.."}[24h])) or vector(0)) / sum(increase(patrol_http_requests_total[24h]))) / 0.005', "")],
              unit="percentunit", defaults={"min": 0, "max": 1, "thresholds": {"mode": "absolute", "steps": [
                  {"color": "red", "value": None}, {"color": "orange", "value": 0.25}, {"color": "green", "value": 0.5}]}}),
        panel(3, "Freshness SLI, last 24h (target 99%)", "stat", 12, 0, 6, 5,
              [('1 - avg_over_time(patrol:checks_stale:ratio[24h])', "")],
              unit="percentunit", defaults={"decimals": 3, "thresholds": {"mode": "absolute", "steps": [
                  {"color": "red", "value": None}, {"color": "green", "value": 0.99}]}},
              desc="Share of time every site's latest check was under 3 minutes old."),
        panel(4, "Freshness error budget left (24h)", "gauge", 18, 0, 6, 5,
              [('1 - avg_over_time(patrol:checks_stale:ratio[24h]) / 0.01', "")],
              unit="percentunit", defaults={"min": 0, "max": 1, "thresholds": {"mode": "absolute", "steps": [
                  {"color": "red", "value": None}, {"color": "orange", "value": 0.25}, {"color": "green", "value": 0.5}]}}),
        panel(5, "Availability burn rate (1 = spending budget exactly on schedule)", "timeseries", 0, 5, 12, 9,
              [('patrol:status_page_errors:ratio_rate5m / 0.005', "5m"),
               ('patrol:status_page_errors:ratio_rate1h / 0.005', "1h"),
               ('patrol:status_page_errors:ratio_rate6h / 0.005', "6h")],
              defaults={"thresholds": {"mode": "absolute", "steps": [
                  {"color": "green", "value": None}, {"color": "orange", "value": 6}, {"color": "red", "value": 14.4}]},
                  "custom": {"thresholdsStyle": {"mode": "line"}}},
              desc="Fast-burn alert: 1h AND 5m above 14.4. Slow-burn: 6h AND 30m above 6."),
        panel(6, "Freshness burn rate", "timeseries", 12, 5, 12, 9,
              [('patrol:checks_stale:ratio_avg5m / 0.01', "5m"),
               ('patrol:checks_stale:ratio_avg1h / 0.01', "1h"),
               ('patrol:checks_stale:ratio_avg6h / 0.01', "6h")],
              defaults={"thresholds": {"mode": "absolute", "steps": [
                  {"color": "green", "value": None}, {"color": "orange", "value": 6}, {"color": "red", "value": 14.4}]},
                  "custom": {"thresholdsStyle": {"mode": "line"}}}),
    ])

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, d in (("patrol-overview.json", patrol), ("patrol-slo.json", slo)):
        (OUT / name).write_text(json.dumps(d, indent=2, sort_keys=True) + "\n")
        print("wrote", OUT / name)
