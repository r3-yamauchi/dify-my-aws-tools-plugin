"""
場所: tools/cf_create_invalidation.py
内容: CloudFront の create_invalidation を呼び出す Dify ツール。
目的: 配信キャッシュをワークフローから安全に無効化し、静的コンテンツ更新を即時反映できるようにする。
"""

from __future__ import annotations

import ast
import json
import random
from collections.abc import Generator
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

try:  # pragma: no cover - パッケージ化時の相対パス差異に対応
    from my_aws_tools.provider.utils import (
        build_boto3_client_kwargs,
        resolve_aws_credentials,
        reset_clients_on_credential_change,
    )
except ModuleNotFoundError:  # pragma: no cover
    from provider.utils import build_boto3_client_kwargs, resolve_aws_credentials, reset_clients_on_credential_change


def _default_invalidation_batch(caller_reference: str | None = None) -> dict[str, Any]:
    """CloudFront API が要求する最小構成で全パス無効化するバッチを返す。"""

    caller_reference = caller_reference or _generate_caller_reference()
    return {
        "Paths": {
            "Items": ["/*"],
            "Quantity": 1,
        },
        "CallerReference": caller_reference,
    }


def _generate_caller_reference() -> str:
    """CloudFront CallerReference を一意に生成するヘルパ。"""

    return f"dify-{int(time.time())}-{random.randint(100000, 999999)}"


def _parse_paths(raw_value: Any) -> tuple[list[str] | None, str | None]:
    """文字列/リストから無効化パスのリストを抽出する。"""

    if raw_value in (None, ""):
        return None, None

    if isinstance(raw_value, list):
        paths = raw_value
    elif isinstance(raw_value, str):
        try:
            paths = json.loads(raw_value)
        except json.JSONDecodeError:
            try:
                paths = ast.literal_eval(raw_value)
            except (ValueError, SyntaxError):
                return None, "paths must be a JSON array or Python-style list string"
    else:
        return None, "paths must be a list or string"

    if not isinstance(paths, list) or not paths:
        return None, "paths must be a non-empty list"
    if not all(isinstance(item, str) and item for item in paths):
        return None, "paths items must be non-empty strings"
    return paths, None


def _parse_invalidation_batch(raw_value: Any, caller_reference: str | None) -> tuple[dict[str, Any] | None, str | None]:
    """invalidation_batch を辞書化する。補完は次フェーズのため必須項目が欠けていればエラーを返す。"""

    if raw_value in (None, ""):
        return _default_invalidation_batch(caller_reference), None

    if isinstance(raw_value, dict):
        batch = raw_value
    elif isinstance(raw_value, str):
        try:
            batch = json.loads(raw_value)
        except json.JSONDecodeError as exc:
            return None, f"invalidation_batch must be valid JSON: {exc}"
    else:
        return None, "invalidation_batch must be a JSON object or string"

    if not isinstance(batch, dict):
        return None, "invalidation_batch must be a JSON object"

    paths = batch.get("Paths")
    if not isinstance(paths, dict):
        return None, "invalidation_batch.Paths is required"
    if not isinstance(paths.get("Items"), list) or not paths.get("Items"):
        return None, "invalidation_batch.Paths.Items must be a non-empty list"
    if "Quantity" not in paths:
        paths["Quantity"] = len(paths["Items"])

    if not batch.get("CallerReference"):
        batch["CallerReference"] = caller_reference or _generate_caller_reference()

    return batch, None


class CfCreateInvalidation(Tool):
    cloudfront_client: Any | None = None

    def _ensure_client(self, credentials: dict[str, Any]) -> None:
        reset_clients_on_credential_change(self, credentials, ["cloudfront_client"])
        if not self.cloudfront_client:
            client_kwargs = build_boto3_client_kwargs(credentials)
            self.cloudfront_client = boto3.client("cloudfront", **client_kwargs)

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            self._ensure_client(credentials)
        except Exception as exc:  # pragma: no cover - boto3 初期化失敗時
            yield self.create_text_message(f"Failed to initialize AWS client: {exc}")
            return

        distribution_id = (tool_parameters.get("distribution_id") or "").strip()
        if not distribution_id:
            yield self.create_text_message("distribution_id parameter is required")
            return

        caller_reference = (tool_parameters.get("caller_reference") or "").strip() or None

        paths, paths_error = _parse_paths(tool_parameters.get("paths"))
        if paths_error:
            yield self.create_text_message(paths_error)
            return

        if paths is not None:
            batch = {
                "Paths": {"Items": paths, "Quantity": len(paths)},
                "CallerReference": caller_reference or _generate_caller_reference(),
            }
        else:
            batch, error = _parse_invalidation_batch(tool_parameters.get("invalidation_batch"), caller_reference)
            if error:
                yield self.create_text_message(error)
                return

        try:
            response = self.cloudfront_client.create_invalidation(
                DistributionId=distribution_id,
                InvalidationBatch=batch,
            )
        except ClientError as exc:
            message = exc.response.get("Error", {}).get("Message", str(exc))
            yield self.create_text_message(f"Failed to create invalidation: {message}")
            return
        except Exception as exc:  # pragma: no cover - 想定外
            yield self.create_text_message(f"Failed to create invalidation: {exc}")
            return

        invalidation = response.get("Invalidation", {})
        payload: dict[str, Any] = {
            "distribution_id": distribution_id,
            "invalidation_id": invalidation.get("Id"),
            "status": invalidation.get("Status"),
            "caller_reference": invalidation.get("InvalidationBatch", {}).get("CallerReference"),
            "create_time": getattr(invalidation.get("CreateTime"), "isoformat", lambda: None)(),
            "paths": invalidation.get("InvalidationBatch", {}).get("Paths"),
        }

        yield self.create_json_message(payload)

        summary = f"Invalidation request submitted: {payload['invalidation_id']} (status: {payload['status']})"
        yield self.create_text_message(summary)
