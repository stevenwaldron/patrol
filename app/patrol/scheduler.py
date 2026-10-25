"""Scheduler: post one patrol order per site to SQS, then exit.

Runs once per invocation. In Kubernetes a CronJob starts it every minute.
It has no /metrics page on purpose: a job that lives for a second can't be
scraped. Its health is measured downstream instead: if orders stop arriving,
the status page's "last checked age" metric climbs and an alert fires.
"""

import json
import logging
import sys
from datetime import datetime, timezone

import boto3

from .config import Settings
from .logs import log, setup_logging
from .sites import Site, load_sites
from .store import to_iso

BATCH_SIZE = 10  # SQS maximum per SendMessageBatch call


def build_orders(sites: list[Site], now: datetime) -> list[dict]:
    issued_at = to_iso(now)
    return [
        {"site_id": s.id, "url": s.url, "timeout_seconds": s.timeout_seconds, "issued_at": issued_at}
        for s in sites
    ]


def send_orders(sqs, queue_url: str, orders: list[dict]) -> tuple[int, list[str]]:
    """Send in batches of 10. Returns (sent count, site_ids that failed)."""
    sent, failed = 0, []
    for start in range(0, len(orders), BATCH_SIZE):
        batch = orders[start:start + BATCH_SIZE]
        entries = [{"Id": str(i), "MessageBody": json.dumps(o)} for i, o in enumerate(batch)]
        resp = sqs.send_message_batch(QueueUrl=queue_url, Entries=entries)
        sent += len(resp.get("Successful", []))
        failed += [batch[int(f["Id"])]["site_id"] for f in resp.get("Failed", [])]
    return sent, failed


def main() -> int:
    logger = setup_logging("scheduler")
    settings = Settings.from_env()
    settings.require("queue_url")

    sites = load_sites(settings.sites_file)
    sqs = boto3.client("sqs", region_name=settings.region)
    orders = build_orders(sites, datetime.now(timezone.utc))
    sent, failed = send_orders(sqs, settings.queue_url, orders)

    level = logging.ERROR if failed else logging.INFO
    log(logger, level, "patrol orders issued", sites=len(sites), sent=sent, failed=failed)
    return 1 if failed else 0  # non-zero exit marks the CronJob run as failed


if __name__ == "__main__":
    sys.exit(main())
