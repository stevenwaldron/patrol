"""Shared test fixtures: fake AWS (moto) and a fake HTTP layer.

Nothing here touches real AWS or the internet.
"""

import os

import boto3
import httpx
import pytest
from moto import mock_aws

# Dummy credentials so boto3 can never reach a real account from a test run.
for key, value in {
    "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing", "AWS_DEFAULT_REGION": "us-east-1",
}.items():
    os.environ[key] = value
os.environ.pop("AWS_PROFILE", None)

REGION = "us-east-1"


@pytest.fixture
def aws():
    with mock_aws():
        yield


@pytest.fixture
def queue_url(aws):
    sqs = boto3.client("sqs", region_name=REGION)
    return sqs.create_queue(QueueName="patrol-checks")["QueueUrl"]


@pytest.fixture
def sqs(aws):
    return boto3.client("sqs", region_name=REGION)


@pytest.fixture
def table(aws):
    ddb = boto3.resource("dynamodb", region_name=REGION)
    return ddb.create_table(
        TableName="patrol-checks",
        KeySchema=[{"AttributeName": "site_id", "KeyType": "HASH"},
                   {"AttributeName": "checked_at", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "site_id", "AttributeType": "S"},
                              {"AttributeName": "checked_at", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


def fake_http(routes: dict):
    """An httpx transport that answers from a dict: url -> status code or exception."""
    def handler(request: httpx.Request) -> httpx.Response:
        outcome = routes.get(str(request.url).rstrip("/"))
        if outcome is None:
            raise httpx.ConnectError("name resolution failed", request=request)
        if isinstance(outcome, Exception):
            raise outcome
        return httpx.Response(outcome, request=request)
    return httpx.MockTransport(handler)
