"""Settings, read from environment variables.

Kubernetes passes configuration as environment variables, so the code never
contains account-specific values. Each service asks only for what it needs and
fails at startup with a clear message if something is missing.
"""

import os
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    return value if value not in ("", None) else default


@dataclass(frozen=True)
class Settings:
    region: str
    queue_url: str | None
    table_name: str | None
    sites_file: str
    result_retention_days: int
    max_order_age_seconds: int
    receive_wait_seconds: int
    worker_threads: int
    health_port: int
    summary_cache_seconds: int
    stale_after_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            region=_env("AWS_REGION", "us-east-1"),
            queue_url=_env("PATROL_QUEUE_URL"),
            table_name=_env("PATROL_TABLE_NAME"),
            sites_file=_env("PATROL_SITES_FILE", "config/sites.yaml"),
            result_retention_days=int(_env("PATROL_RESULT_RETENTION_DAYS", "30")),
            max_order_age_seconds=int(_env("PATROL_MAX_ORDER_AGE_SECONDS", "120")),
            receive_wait_seconds=int(_env("PATROL_RECEIVE_WAIT_SECONDS", "20")),
            worker_threads=int(_env("PATROL_WORKER_THREADS", "10")),
            health_port=int(_env("PATROL_HEALTH_PORT", "9100")),
            summary_cache_seconds=int(_env("PATROL_SUMMARY_CACHE_SECONDS", "30")),
            stale_after_seconds=int(_env("PATROL_STALE_AFTER_SECONDS", "300")),
        )

    def require(self, *fields: str) -> None:
        """Fail fast if a service is started without the settings it needs."""
        env_names = {"queue_url": "PATROL_QUEUE_URL", "table_name": "PATROL_TABLE_NAME"}
        missing = [env_names.get(f, f) for f in fields if not getattr(self, f)]
        if missing:
            raise SystemExit(f"Missing required environment variable(s): {', '.join(missing)}")
