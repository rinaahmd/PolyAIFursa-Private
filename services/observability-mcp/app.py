
from __future__ import annotations

import gzip
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import boto3
import requests
from dateutil.parser import isoparse
from fastmcp import FastMCP


Environment = Literal["dev", "prod"]

mcp = FastMCP("PolyAI Observability")


def _prometheus_url(environment: Environment) -> str:
    variable = (
        "DEV_PROMETHEUS_URL"
        if environment == "dev"
        else "PROD_PROMETHEUS_URL"
    )

    value = os.getenv(variable)

    if not value:
        raise ValueError(f"Missing environment variable: {variable}")

    return value.rstrip("/")


def _prometheus_request(
    environment: Environment,
    endpoint: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    url = f"{_prometheus_url(environment)}{endpoint}"

    try:
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(
            f"Could not connect to {environment} Prometheus: {exc}"
        ) from exc
    except ValueError as exc:
        raise RuntimeError(
            "Prometheus returned invalid JSON"
        ) from exc

    if payload.get("status") != "success":
        raise RuntimeError(
            f"Prometheus query failed: {payload.get('error', payload)}"
        )

    return payload["data"]


def _s3_bucket(environment: Environment) -> str:
    variable = (
        "DEV_S3_LOGS_BUCKET"
        if environment == "dev"
        else "PROD_S3_LOGS_BUCKET"
    )

    bucket = os.getenv(variable)

    if not bucket:
        raise ValueError(f"Missing environment variable: {variable}")

    return bucket


def _s3_client():
    return boto3.client(
        "s3",
        region_name=os.getenv("AWS_REGION", "us-east-1"),
    )


def _read_s3_text(bucket: str, key: str) -> str:
    response = _s3_client().get_object(
        Bucket=bucket,
        Key=key,
    )

    content = response["Body"].read()

    try:
        content = gzip.decompress(content)
    except gzip.BadGzipFile:
        pass

    return content.decode("utf-8", errors="replace")


def _list_recent_log_objects(
    environment: Environment,
    minutes: int,
    since: datetime | None = None,
) -> list[dict[str, Any]]:
    bucket = _s3_bucket(environment)
    prefix = f"{environment}/logs/"

    # Fluent Bit may upload a file slightly after the log was created.
    if since is not None:
        object_cutoff = since - timedelta(minutes=2)
    else:
        object_cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes + 2)

    objects: list[dict[str, Any]] = []

    paginator = _s3_client().get_paginator("list_objects_v2")

    for page in paginator.paginate(
        Bucket=bucket,
        Prefix=prefix,
    ):
        for item in page.get("Contents", []):
            if item["LastModified"] >= object_cutoff:
                objects.append(item)

    # Protect the MCP from reading too many files at once.
    objects.sort(
        key=lambda item: item["LastModified"],
        reverse=True,
    )

    return objects[:200]


def _parse_record_time(record: dict[str, Any]) -> datetime | None:
    value = record.get("time") or record.get("date")

    if not value:
        return None

    try:
        return isoparse(str(value)).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def _extract_service_from_key(key: str) -> str | None:
    match = re.search(r"/service=([^/]+)/", key)
    return match.group(1) if match else None


def _recent_log_records(
    environment: Environment,
    minutes: int,
    service: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict[str, Any]]:
    bucket = _s3_bucket(environment)
    reference = since if since is not None else datetime.now(timezone.utc)
    cutoff = reference - timedelta(minutes=minutes)

    records: list[dict[str, Any]] = []

    for item in _list_recent_log_objects(environment, minutes, since=since):
        if service is not None and _extract_service_from_key(item["Key"]) != service:
            continue

        text = _read_s3_text(bucket, item["Key"])

        for line in text.splitlines():
            if not line.strip():
                continue

            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            record_time = _parse_record_time(record)

            if record_time and record_time < cutoff:
                continue

            if until is not None and record_time and record_time > until:
                continue

            record["_s3_key"] = item["Key"]
            records.append(record)

    records.sort(
        key=lambda record: _parse_record_time(record)
        or datetime.min.replace(tzinfo=timezone.utc)
    )

    return records


@mcp.tool
def query_prometheus(
    environment: Environment,
    query: str,
) -> dict[str, Any]:
    """Run an instant PromQL query against Dev or Prod."""

    return _prometheus_request(
        environment,
        "/api/v1/query",
        {"query": query},
    )


@mcp.tool
def query_prometheus_range(
    environment: Environment,
    query: str,
    minutes: int = 10,
    step_seconds: int = 30,
) -> dict[str, Any]:
    """Run a PromQL query over a recent time range."""

    if minutes < 1 or minutes > 1440:
        raise ValueError("minutes must be between 1 and 1440")

    if step_seconds < 1 or step_seconds > 3600:
        raise ValueError("step_seconds must be between 1 and 3600")

    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)

    return _prometheus_request(
        environment,
        "/api/v1/query_range",
        {
            "query": query,
            "start": start.timestamp(),
            "end": end.timestamp(),
            "step": step_seconds,
        },
    )


@mcp.tool
def list_containers_shipping_logs(
    environment: Environment,
    minutes: int = 60,
) -> dict[str, Any]:
    """
    List containers whose log records appeared in S3 recently.
    """

    if minutes < 1 or minutes > 1440:
        raise ValueError("minutes must be between 1 and 1440")

    services: set[str] = set()

    for item in _list_recent_log_objects(environment, minutes):
        service = _extract_service_from_key(item["Key"])
        if service:
            services.add(service)

    return {
        "environment": environment,
        "minutes": minutes,
        "count": len(services),
        "services": sorted(services),
    }


@mcp.tool
def get_container_logs(
    environment: Environment,
    service: str,
    minutes: int = 5,
    max_lines: int = 200,
    around_time: str | None = None,
) -> dict[str, Any]:
    """
    Return recent S3 logs for a service such as yolo, agent or frontend.
    Pass around_time as an ISO 8601 string (e.g. "2026-07-01T12:00:00Z")
    to query logs around a specific point in time instead of the last N minutes.
    """

    if minutes < 1 or minutes > 1440:
        raise ValueError("minutes must be between 1 and 1440")

    if max_lines < 1 or max_lines > 1000:
        raise ValueError("max_lines must be between 1 and 1000")

    since: datetime | None = None
    until: datetime | None = None

    if around_time is not None:
        try:
            pivot = isoparse(around_time).astimezone(timezone.utc)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid around_time: {exc}") from exc
        since = pivot - timedelta(minutes=minutes)
        until = pivot + timedelta(minutes=minutes)

    results: list[dict[str, Any]] = []

    for record in _recent_log_records(
        environment,
        minutes,
        service=service.lower(),
        since=since,
        until=until,
    ):
        results.append(
            {
                "time": record.get("time") or record.get("date"),
                "service": _extract_service_from_key(record.get("_s3_key", "")) or service,
                "stream": record.get("stream"),
                "log": str(record.get("log", "")).rstrip(),
                "s3_key": record.get("_s3_key"),
            }
        )

    return {
        "environment": environment,
        "service": service,
        "minutes": minutes,
        "around_time": around_time,
        "count": len(results[-max_lines:]),
        "logs": results[-max_lines:],
    }


if __name__ == "__main__":
    mcp.run()
