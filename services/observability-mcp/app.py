
from __future__ import annotations

import gzip
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import boto3
import requests
from botocore.exceptions import ClientError
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


def _container_metadata(
    environment: Environment,
) -> list[dict[str, str]]:
    bucket = _s3_bucket(environment)
    key = f"{environment}/container-metadata/containers.jsonl"

    try:
        text = _read_s3_text(bucket, key)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")

        if code in {"NoSuchKey", "404"}:
            return []

        raise

    containers: list[dict[str, str]] = []

    for line in text.splitlines():
        if not line.strip():
            continue

        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue

        containers.append(
            {
                "id": str(item.get("ID", "")),
                "name": str(item.get("Names", "")),
                "image": str(item.get("Image", "")),
                "status": str(item.get("Status", "")),
            }
        )

    return containers


def _list_recent_log_objects(
    environment: Environment,
    minutes: int,
) -> list[dict[str, Any]]:
    bucket = _s3_bucket(environment)
    prefix = f"{environment}/logs/"

    # Fluent Bit may upload a file slightly after the log was created.
    object_cutoff = datetime.now(timezone.utc) - timedelta(
        minutes=minutes + 2
    )

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


def _extract_container_id(source_file: str) -> str | None:
    match = re.search(
        r"/containers/([0-9a-fA-F]+)/",
        source_file,
    )

    return match.group(1) if match else None


def _recent_log_records(
    environment: Environment,
    minutes: int,
) -> list[dict[str, Any]]:
    bucket = _s3_bucket(environment)
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)

    records: list[dict[str, Any]] = []

    for item in _list_recent_log_objects(environment, minutes):
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

            record["_s3_key"] = item["Key"]
            records.append(record)

    records.sort(
        key=lambda record: _parse_record_time(record)
        or datetime.min.replace(tzinfo=timezone.utc)
    )

    return records


def _container_name(
    full_container_id: str,
    metadata: list[dict[str, str]],
) -> str:
    for container in metadata:
        short_id = container["id"]

        if short_id and full_container_id.startswith(short_id):
            return container["name"]

    return full_container_id[:12]


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

    metadata = _container_metadata(environment)
    records = _recent_log_records(environment, minutes)

    containers: dict[str, dict[str, Any]] = {}

    for record in records:
        source_file = str(record.get("source_file", ""))
        container_id = _extract_container_id(source_file)

        if not container_id:
            continue

        name = _container_name(container_id, metadata)

        containers[container_id] = {
            "container_id": container_id[:12],
            "container_name": name,
            "environment": environment,
        }

    return {
        "environment": environment,
        "minutes": minutes,
        "count": len(containers),
        "containers": list(containers.values()),
    }


@mcp.tool
def get_container_logs(
    environment: Environment,
    service: str,
    minutes: int = 5,
    max_lines: int = 200,
) -> dict[str, Any]:
    """
    Return recent S3 logs for a service such as yolo, agent or frontend.
    """

    if minutes < 1 or minutes > 1440:
        raise ValueError("minutes must be between 1 and 1440")

    if max_lines < 1 or max_lines > 1000:
        raise ValueError("max_lines must be between 1 and 1000")

    metadata = _container_metadata(environment)
    service_lower = service.lower()

    matched_containers = [
        container
        for container in metadata
        if service_lower in container["name"].lower()
        or service_lower in container["image"].lower()
    ]

    if not matched_containers:
        available = [
            container["name"]
            for container in metadata
        ]

        raise ValueError(
            f"No container matched service '{service}'. "
            f"Available containers: {available}"
        )

    matching_ids = [
        container["id"]
        for container in matched_containers
        if container["id"]
    ]

    results: list[dict[str, Any]] = []

    for record in _recent_log_records(environment, minutes):
        source_file = str(record.get("source_file", ""))
        container_id = _extract_container_id(source_file)

        if not container_id:
            continue

        if not any(
            container_id.startswith(short_id)
            for short_id in matching_ids
        ):
            continue

        results.append(
            {
                "time": record.get("time") or record.get("date"),
                "container": _container_name(
                    container_id,
                    metadata,
                ),
                "stream": record.get("stream"),
                "log": str(record.get("log", "")).rstrip(),
                "s3_key": record.get("_s3_key"),
            }
        )

    return {
        "environment": environment,
        "service": service,
        "minutes": minutes,
        "count": len(results[-max_lines:]),
        "logs": results[-max_lines:],
    }


if __name__ == "__main__":
    mcp.run()
