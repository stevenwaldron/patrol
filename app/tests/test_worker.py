import json
from datetime import datetime, timedelta, timezone

import pytest

from patrol.checker import make_client
from patrol.store import ResultStore, to_iso
from patrol.worker import Worker
from tests.conftest import fake_http

SITE_URL = "https://site.example"


def send(sqs, queue_url, body):
    sqs.send_message(QueueUrl=queue_url, MessageBody=body if isinstance(body, str) else json.dumps(body))


def order(site_id="site", age_seconds=0, url=SITE_URL):
    issued = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return {"site_id": site_id, "url": url, "timeout_seconds": 5, "issued_at": to_iso(issued)}


def queue_depth(sqs, queue_url):
    attrs = sqs.get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=["ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"],
    )["Attributes"]
    return int(attrs["ApproximateNumberOfMessages"]) + int(attrs["ApproximateNumberOfMessagesNotVisible"])


@pytest.fixture
def make_worker(sqs, queue_url, table):
    def _make(routes=None, store=None):
        return Worker(sqs, queue_url, store or ResultStore(table),
                      make_client(fake_http(routes or {SITE_URL: 200})),
                      wait_seconds=0, name="test-worker")
    return _make


def test_up_result_saved_and_order_deleted(sqs, queue_url, table, make_worker):
    send(sqs, queue_url, order())
    assert make_worker().poll_once() == 1
    item = ResultStore(table).latest("site")
    assert item["status"] == "up" and item["http_status"] == 200 and item["worker"] == "test-worker"
    assert item["expires_at"] > datetime.now(timezone.utc).timestamp()
    assert queue_depth(sqs, queue_url) == 0


def test_down_site_is_a_successful_check(sqs, queue_url, table, make_worker):
    send(sqs, queue_url, order(url="https://down.invalid"))
    make_worker().poll_once()
    item = ResultStore(table).latest("site")
    assert item["status"] == "down" and "ConnectError" in item["error"]
    assert queue_depth(sqs, queue_url) == 0  # recorded, so the order is done


def test_stale_order_skipped_not_saved(sqs, queue_url, table, make_worker):
    send(sqs, queue_url, order(age_seconds=600))
    make_worker().poll_once()
    assert ResultStore(table).latest("site") is None
    assert queue_depth(sqs, queue_url) == 0


def test_invalid_order_deleted(sqs, queue_url, table, make_worker):
    send(sqs, queue_url, "not json at all")
    make_worker().poll_once()
    assert queue_depth(sqs, queue_url) == 0


def test_save_failure_leaves_order_for_retry(sqs, queue_url, table, make_worker):
    class BrokenStore:
        def save(self, *_a, **_k):
            raise RuntimeError("DynamoDB unavailable")

    send(sqs, queue_url, order())
    make_worker(store=BrokenStore()).poll_once()
    assert queue_depth(sqs, queue_url) == 1  # still there: SQS will redeliver it


def test_health_reflects_heartbeat(make_worker):
    worker = make_worker()
    assert worker.is_healthy(max_silence=60)
    worker.last_heartbeat -= 120
    assert not worker.is_healthy(max_silence=60)
