import json
from datetime import datetime, timezone

from patrol.scheduler import build_orders, send_orders
from patrol.sites import Site


def receive_all(sqs, queue_url):
    bodies = []
    while True:
        msgs = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10).get("Messages", [])
        if not msgs:
            return bodies
        for m in msgs:
            bodies.append(json.loads(m["Body"]))
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=m["ReceiptHandle"])


def test_one_order_per_site_across_batches(sqs, queue_url):
    sites = [Site(f"site-{i}", f"Site {i}", f"https://s{i}.example") for i in range(23)]
    sent, failed = send_orders(sqs, queue_url, build_orders(sites, datetime.now(timezone.utc)))
    assert sent == 23 and failed == []           # 23 orders = 3 batches (10 + 10 + 3)
    bodies = receive_all(sqs, queue_url)
    assert sorted(b["site_id"] for b in bodies) == sorted(s.id for s in sites)


def test_order_contents():
    now = datetime(2026, 10, 19, 23, 40, 1, 123456, tzinfo=timezone.utc)
    [order] = build_orders([Site("a", "A", "https://a.example", 7)], now)
    assert order == {"site_id": "a", "url": "https://a.example", "timeout_seconds": 7,
                     "issued_at": "2026-10-19T23:40:01.123456Z"}
