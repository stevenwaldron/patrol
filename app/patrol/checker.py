"""The actual check: visit a URL once and record what happened."""

import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

USER_AGENT = "Patrol-Uptime-Monitor/0.1 (+portfolio project)"


@dataclass(frozen=True)
class CheckResult:
    site_id: str
    checked_at: datetime
    up: bool
    http_status: int | None
    latency_ms: int
    error: str | None


def make_client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """One shared client, so connections are reused between checks."""
    return httpx.Client(
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
        transport=transport,
    )


def check_site(client: httpx.Client, site_id: str, url: str, timeout: float) -> CheckResult:
    """GET the URL. "Up" means the final response (after redirects) is 2xx or 3xx.

    GET rather than HEAD: some servers reject HEAD with 405, which would be a
    false "down".
    """
    checked_at = datetime.now(timezone.utc)
    start = time.perf_counter()
    status, error = None, None
    try:
        response = client.get(url, timeout=timeout)
        status = response.status_code
        up = 200 <= status < 400
        if not up:
            error = f"HTTP {status}"
    except httpx.TimeoutException:
        up, error = False, f"timeout after {timeout:g}s"
    except httpx.HTTPError as exc:
        up, error = False, f"{type(exc).__name__}: {exc}"[:200]
    latency_ms = int((time.perf_counter() - start) * 1000)
    return CheckResult(site_id, checked_at, up, status, latency_ms, error)
