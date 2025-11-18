"""
場所: tools/s3_list_objects.py
内容: 特定の S3 バケット内オブジェクトを一覧取得し、ワークフローで再利用可能な JSON/テキストを返すツール。
目的: バケット配下のキー構造を確認し、後続ノードのダウンロードや加工処理に必要な情報を素早く取得する。
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import boto3
from botocore.exceptions import ClientError

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

try:  # pragma: no cover - 発行パッケージから参照される場合のフォールバック
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
    """datetime を ISO 8601 文字列に変換する安全なヘルパ。"""

    try:
        return value.isoformat()
    except AttributeError:
        return None


class S3ListObjects(Tool):
    s3_client: Any = None

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """指定バケットのオブジェクトを一覧取得して返す。"""

        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            if tool_parameters.get("aws_region"):
                credentials["aws_region"] = tool_parameters["aws_region"]

            reset_clients_on_credential_change(self, credentials, ["s3_client"])
            if not self.s3_client:
                client_kwargs = build_boto3_client_kwargs(credentials)
                self.s3_client = boto3.client("s3", **client_kwargs)
        except Exception as exc:  # pragma: no cover - boto3 初期化エラー
            yield self.create_text_message(f"Failed to initialize AWS client: {exc}")
            return

        bucket_name = (tool_parameters.get("bucket_name") or "").strip()
        if not bucket_name:
            yield self.create_text_message("bucket_name parameter is required")
            return

        prefix = (tool_parameters.get("prefix") or "").strip()
        continuation_token = (tool_parameters.get("continuation_token") or "").strip() or None

        max_keys_raw = tool_parameters.get("max_keys", 100)
        try:
            max_keys = int(max_keys_raw)
        except (TypeError, ValueError):
            yield self.create_text_message("max_keys must be an integer")
            return
        max_keys = max(1, min(max_keys, 1000))  # API 制限: 1 <= MaxKeys <= 1000

        request_kwargs: dict[str, Any] = {"Bucket": bucket_name, "MaxKeys": max_keys}
        if prefix:
            request_kwargs["Prefix"] = prefix
        if continuation_token:
            request_kwargs["ContinuationToken"] = continuation_token

        try:
            response = self.s3_client.list_objects_v2(**request_kwargs)
        except self.s3_client.exceptions.NoSuchBucket:
            yield self.create_text_message(f"Bucket '{bucket_name}' does not exist")
            return
        except ClientError as exc:
            error_message = exc.response.get("Error", {}).get("Message", str(exc))
            yield self.create_text_message(f"Failed to list objects: {error_message}")
            return
        except Exception as exc:  # pragma: no cover - 想定外エラー
            yield self.create_text_message(f"Failed to list objects: {exc}")
            return

        objects: list[dict[str, Any]] = []
        for entry in response.get("Contents", []) or []:
            objects.append(
                {
                    "key": entry.get("Key"),
                    "size": entry.get("Size"),
                    "last_modified": _to_iso8601(entry.get("LastModified")),
                    "etag": entry.get("ETag"),
                    "storage_class": entry.get("StorageClass"),
                }
            )

        payload = {
            "bucket_name": bucket_name,
            "prefix": prefix or None,
            "max_keys": max_keys,
            "continuation_token": continuation_token,
            "is_truncated": response.get("IsTruncated", False),
            "next_continuation_token": response.get("NextContinuationToken"),
            "object_count": len(objects),
            "objects": objects,
        }

        yield self.create_json_message(payload)

        if not objects:
            summary = f"No objects found in bucket '{bucket_name}' for the current filter."
        else:
            sample_keys = ", ".join(obj["key"] for obj in objects[:5] if obj.get("key"))
            summary = f"{len(objects)} object(s) listed. Sample: {sample_keys}"
        yield self.create_text_message(summary)
