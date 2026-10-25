from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from patrol.checker import CheckResult
from patrol.sites import Site
from patrol.status_page import create_app, summarize_site
from patrol.store import ResultStore

NOW = datetime.now(timezone.utc)
SITES = [Site("alpha", "Alpha <b>site</b>", "https://alpha.example"),
         Site("beta", "Beta", "https://beta.example"),
         Site("gamma", "Gamma", "https://gamma.example")]


def seed(store, site_id, statuses, minutes_ago_start):
    """Save one check per minute, oldest first."""
    for i, up in enumerate(statuses):
        at = NOW - timedelta(minutes=minutes_ago_start - i)
        store.save(CheckResult(site_id, at, up, 200 if up else 503, 100 + i, None if up else "HTTP 503"),
                   worker="seed")


@pytest.fixture
def client(table):
    store = ResultStore(table)
    seed(store, "alpha", [True, True, True, False], minutes_ago_start=4)   # down now, 75% uptime
    seed(store, "beta", [True] * 4, minutes_ago_start=30)                  # last check 27 min ago: stale
    # gamma never checked
    return TestClient(create_app(store, SITES, cache_seconds=0, stale_after=300))


def test_summary_statuses(client):
    sites = {s["id"]: s for s in client.get("/api/sites").json()["sites"]}
    assert sites["alpha"]["status"] == "down"
    assert sites["alpha"]["uptime_24h_percent"] == 75.0
    assert sites["alpha"]["error"] == "HTTP 503"
    assert sites["beta"]["status"] == "stale"
    assert sites["gamma"]["status"] == "unknown" and sites["gamma"]["checks_24h"] == 0


def test_history_and_unknown_site(client):
    body = client.get("/api/sites/alpha/history?hours=1").json()
    assert len(body["checks"]) == 4
    assert body["checks"][0]["checked_at"] < body["checks"][-1]["checked_at"]  # oldest first
    assert client.get("/api/sites/nope/history").status_code == 404
    assert client.get("/api/sites/alpha/history?hours=999").status_code == 422


def test_probes(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/ready").json()["sites"] == 3


def test_metrics_expose_site_state(client):
    text = client.get("/metrics").text
    assert 'patrol_site_up{site="alpha"} 0.0' in text
    assert 'patrol_site_uptime_ratio_24h{site="alpha"} 0.75' in text
    assert 'patrol_site_last_checked_age_seconds{site="gamma"} +Inf' in text


def test_request_metrics_use_route_template_and_skip_probes(client):
    client.get("/api/sites/alpha/history")
    client.get("/health")
    text = client.get("/metrics").text
    assert 'path="/api/sites/{site_id}/history"' in text   # template, not the raw URL
    assert 'path="/health"' not in text                    # probes don't count toward the SLO


def test_html_page_escapes_names(client):
    page = client.get("/").text
    assert "Alpha &lt;b&gt;site&lt;/b&gt;" in page and "<b>site</b>" not in page
    assert "Some systems need attention" in page


def test_no_latency_shown_when_site_never_answered(table):
    store = ResultStore(table)
    store.save(CheckResult("alpha", NOW, False, None, 3, "ConnectError: no such host"), worker="t")
    page = TestClient(create_app(store, SITES[:1], cache_seconds=0)).get("/").text
    assert "3 ms" not in page and "—" in page


def test_summarize_with_no_checks():
    s = summarize_site(SITES[0], [], NOW, 300)
    assert s["status"] == "unknown" and s["uptime_24h_percent"] is None
