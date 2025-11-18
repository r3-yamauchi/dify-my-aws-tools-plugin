"""
場所: tools/s3_list_buckets.py
内容: S3 バケットの一覧を取得し、ワークフローで扱いやすい JSON/テキストを返すツール。
目的: Dify 上から保有バケットを可視化し、後続の S3 操作パラメータ入力を簡素化する。
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import boto3
from botocore.exceptions import ClientError

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

try:  # pragma: no cover - import path differs when packaged
    from my_aws_tools.provider.utils import (
        build_boto3_client_kwargs,
        resolve_aws_credentials,
        reset_clients_on_credential_change,
    )
except ModuleNotFoundError:  # pragma: no cover
    from provider.utils import (
        build_boto3_client_kwargs,
        resolve_aws_credentials,
        reset_clients_on_credential_change,
    )


def _to_iso8601(value: Any) -> str | None:
    """datetime を ISO8601 文字列へ安全に変換する。"""

    try:
        return value.isoformat()
    except AttributeError:
        return None


class S3ListBuckets(Tool):
    s3_client: Any = None

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """S3 バケット一覧を取得し、必要に応じてリージョン情報も付与する。"""

        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            if tool_parameters.get("aws_region"):
                credentials["aws_region"] = tool_parameters["aws_region"]

            reset_clients_on_credential_change(self, credentials, ["s3_client"])
            if not self.s3_client:
                client_kwargs = build_boto3_client_kwargs(credentials)
                self.s3_client = boto3.client("s3", **client_kwargs)
        except Exception as exc:  # pragma: no cover - boto3 init errors
            yield self.create_text_message(f"Failed to initialize AWS client: {exc}")
            return

        include_region = bool(tool_parameters.get("include_region"))
        name_prefix = (tool_parameters.get("name_prefix") or "").strip()

        try:
            response = self.s3_client.list_buckets()
        except ClientError as exc:
            error_message = exc.response.get("Error", {}).get("Message", str(exc))
            yield self.create_text_message(f"Failed to list buckets: {error_message}")
            return
        except Exception as exc:  # pragma: no cover - unexpected errors
            yield self.create_text_message(f"Failed to list buckets: {exc}")
            return

        buckets: list[dict[str, Any]] = []
        for bucket in response.get("Buckets", []):
            bucket_name = bucket.get("Name")
            if not bucket_name:
                continue
            if name_prefix and not bucket_name.startswith(name_prefix):
                continue

            bucket_entry: dict[str, Any] = {
                "name": bucket_name,
                "creation_date": _to_iso8601(bucket.get("CreationDate")),
            }

            if include_region:
                try:
                    location_response = self.s3_client.get_bucket_location(Bucket=bucket_name)
                    constraint = location_response.get("LocationConstraint")
                    bucket_entry["region"] = constraint or "us-east-1"
                except ClientError as exc:
                    error_message = exc.response.get("Error", {}).get("Message", str(exc))
                    bucket_entry["region_lookup_error"] = error_message

            buckets.append(bucket_entry)

        payload = {
            "bucket_count": len(buckets),
            "buckets": buckets,
        }
        yield self.create_json_message(payload)

        if not buckets:
            summary = "No buckets matched the current filter."
        else:
            names_text = ", ".join(bucket["name"] for bucket in buckets)
            summary = f"{len(buckets)} bucket(s): {names_text}"
        yield self.create_text_message(summary)
