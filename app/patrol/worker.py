"""Worker: take patrol orders from SQS, check each site, save the result.

Message lifecycle (this is what makes the queue reliable):
  - Result saved      -> message deleted (done).
  - Order too old     -> deleted without checking (a stale check is misleading).
  - Unreadable order  -> deleted and counted (retrying won't fix it).
  - Saving failed     -> NOT deleted. SQS re-delivers it after the visibility
                         timeout; after 3 failures it moves to the dead-letter queue.

A site being down is a *successful* check with a "down" result, not an error.

Also serves, on PATROL_HEALTH_PORT (default 9100):
  /metrics  Prometheus metrics
  /health   200 while the poll loop is alive; 503 if it has stalled (liveness)
"""

import json
import logging
import signal
import socket
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import boto3
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from .checker import check_site, make_client
from .config import Settings
from .logs import log, setup_logging
from .store import ResultStore, from_iso

CHECKS = Counter("patrol_checks_total", "Checks completed, by site and result", ["site", "result"])
CHECK_SECONDS = Histogram(
    "patrol_check_duration_seconds", "Time to check a site", ["site"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)
ORDERS_SKIPPED = Counter("patrol_orders_skipped_total", "Orders deleted without checking", ["reason"])
WORKER_ERRORS = Counter("patrol_worker_errors_total", "Worker errors, by stage", ["stage"])

logger = logging.getLogger("worker")


class Worker:
    def __init__(self, sqs, queue_url, store, http_client, *, max_order_age=120,
                 wait_seconds=20, threads=10, name=None):
        self.sqs, self.queue_url, self.store, self.http = sqs, queue_url, store, http_client
        self.max_order_age = max_order_age
        self.wait_seconds = wait_seconds
        self.threads = threads
        self.name = name or socket.gethostname()  # the pod name in Kubernetes
        self.stop = threading.Event()
        self.last_heartbeat = time.monotonic()

    # -- the loop -----------------------------------------------------------
    def run(self) -> None:
        log(logger, logging.INFO, "worker started", worker=self.name)
        with ThreadPoolExecutor(max_workers=self.threads) as pool:
            while not self.stop.is_set():
                self.poll_once(pool)
        log(logger, logging.INFO, "worker stopped cleanly", worker=self.name)

    def poll_once(self, pool: ThreadPoolExecutor | None = None) -> int:
        """One long-poll for up to 10 orders; handle them in parallel. Returns count."""
        self.last_heartbeat = time.monotonic()
        try:
            resp = self.sqs.receive_message(
                QueueUrl=self.queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=self.wait_seconds,
            )
        except Exception:
            WORKER_ERRORS.labels("receive").inc()
            logger.exception("receive failed", extra={"fields": {}})
            time.sleep(2)  # brief back-off so a broken connection doesn't spin
            return 0
        messages = resp.get("Messages", [])
        if pool:
            list(pool.map(self.handle, messages))
        else:
            for m in messages:
                self.handle(m)
        self.last_heartbeat = time.monotonic()
        return len(messages)

    # -- one order ----------------------------------------------------------
    def handle(self, message: dict) -> None:
        try:
            order = json.loads(message["Body"])
            site_id, url = order["site_id"], order["url"]
            timeout = float(order.get("timeout_seconds", 10))
            issued_at = from_iso(order["issued_at"])
        except (KeyError, ValueError, TypeError):
            ORDERS_SKIPPED.labels("invalid").inc()
            log(logger, logging.WARNING, "invalid order deleted", body=message.get("Body", "")[:200])
            self._delete(message)
            return

        age = (datetime.now(timezone.utc) - issued_at).total_seconds()
        if age > self.max_order_age:
            ORDERS_SKIPPED.labels("stale").inc()
            log(logger, logging.INFO, "stale order skipped", site_id=site_id, age_seconds=round(age))
            self._delete(message)
            return

        result = check_site(self.http, site_id, url, timeout)
        CHECK_SECONDS.labels(site_id).observe(result.latency_ms / 1000)

        try:
            self.store.save(result, worker=self.name)
        except Exception:
            WORKER_ERRORS.labels("save").inc()
            logger.exception("saving result failed; order left for retry",
                             extra={"fields": {"site_id": site_id}})
            return  # not deleted: SQS will redeliver (then dead-letter after 3 tries)

        CHECKS.labels(site_id, "up" if result.up else "down").inc()
        log(logger, logging.INFO, "site checked", site_id=site_id,
            status="up" if result.up else "down", http_status=result.http_status,
            latency_ms=result.latency_ms, error=result.error)
        self._delete(message)

    def _delete(self, message: dict) -> None:
        try:
            self.sqs.delete_message(QueueUrl=self.queue_url, ReceiptHandle=message["ReceiptHandle"])
        except Exception:
            WORKER_ERRORS.labels("delete").inc()
            logger.exception("delete failed", extra={"fields": {}})

    def is_healthy(self, max_silence: float) -> bool:
        return time.monotonic() - self.last_heartbeat < max_silence


def start_health_server(worker: Worker, port: int, max_silence: float) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/metrics":
                body, ctype, code = generate_latest(), CONTENT_TYPE_LATEST, 200
            elif self.path == "/health":
                ok = worker.is_healthy(max_silence)
                body, ctype, code = (b"ok" if ok else b"stalled"), "text/plain", (200 if ok else 503)
            else:
                body, ctype, code = b"not found", "text/plain", 404
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence per-request access logs
            pass

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> int:
    setup_logging("worker")
    settings = Settings.from_env()
    settings.require("queue_url", "table_name")

    table = boto3.resource("dynamodb", region_name=settings.region).Table(settings.table_name)
    worker = Worker(
        sqs=boto3.client("sqs", region_name=settings.region),
        queue_url=settings.queue_url,
        store=ResultStore(table, settings.result_retention_days),
        http_client=make_client(),
        max_order_age=settings.max_order_age_seconds,
        wait_seconds=settings.receive_wait_seconds,
        threads=settings.worker_threads,
    )

    # Kubernetes sends SIGTERM before stopping a pod: finish the current batch, then exit.
    def _stop(signum, _frame):
        log(logger, logging.INFO, "shutdown signal received", signal=signal.Signals(signum).name)
        worker.stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    # Healthy while a poll cycle has completed recently. One cycle can take up to
    # the long-poll wait plus the slowest check, so allow comfortably more.
    max_silence = settings.receive_wait_seconds + 60
    server = start_health_server(worker, settings.health_port, max_silence)
    try:
        worker.run()
    finally:
        server.shutdown()
        worker.http.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
