"""Reading and writing check results in DynamoDB.

Table key: site_id (partition) + checked_at (sort). checked_at is a fixed-width
UTC timestamp, so sorting the strings sorts by time.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from boto3.dynamodb.conditions import Key

from .checker import CheckResult


def to_iso(dt: datetime) -> str:
    """Fixed width, always UTC, always microseconds: e.g. 2026-10-19T23:40:01.123456Z"""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def from_iso(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _plain(item: dict) -> dict:
    """DynamoDB returns numbers as Decimal; convert them for JSON and maths."""
    return {k: (int(v) if isinstance(v, Decimal) else v) for k, v in item.items()}


class ResultStore:
    def __init__(self, table, retention_days: int = 30):
        self.table = table
        self.retention = timedelta(days=retention_days)

    def save(self, result: CheckResult, worker: str) -> None:
        item = {
            "site_id": result.site_id,
            "checked_at": to_iso(result.checked_at),
            "status": "up" if result.up else "down",
            "latency_ms": result.latency_ms,
            "worker": worker,
            # DynamoDB deletes the item on its own after this time (TTL).
            "expires_at": int((result.checked_at + self.retention).timestamp()),
        }
        if result.http_status is not None:
            item["http_status"] = result.http_status
        if result.error:
            item["error"] = result.error
        self.table.put_item(Item=item)

    def latest(self, site_id: str) -> dict | None:
        resp = self.table.query(
            KeyConditionExpression=Key("site_id").eq(site_id),
            ScanIndexForward=False,  # newest first
            Limit=1,
        )
        items = resp.get("Items", [])
        return _plain(items[0]) if items else None

    def history(self, site_id: str, since: datetime) -> list[dict]:
        """All checks for a site since a time, oldest first (follows pagination)."""
        kwargs = {
            "KeyConditionExpression": Key("site_id").eq(site_id) & Key("checked_at").gte(to_iso(since)),
            "ScanIndexForward": True,
        }
        items = []
        while True:
            resp = self.table.query(**kwargs)
            items.extend(_plain(i) for i in resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                return items
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
