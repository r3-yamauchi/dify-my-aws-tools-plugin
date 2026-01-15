"""
場所: tools/cloudwatch_logs_describe_streams.py
内容: CloudWatch Logs の指定したロググループ内のログストリームを検索・一覧取得するツール。
目的: ログストリームの存在確認と基本情報の取得により、後続のログイベント検索処理を効率化する。
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import boto3
from botocore.exceptions import ClientError

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

try:  # pragma: no cover - 発行パッケージから参照される場合のフォールバック
    from utils.utils import (
        build_boto3_client_kwargs,
        resolve_aws_credentials,
        reset_clients_on_credential_change,
    )
    from my_aws_tools.utils import TimeUtils, CloudWatchLogsError
except ModuleNotFoundError:  # pragma: no cover
    from utils.utils import (
        build_boto3_client_kwargs,
        resolve_aws_credentials,
        reset_clients_on_credential_change,
    )
    from utils import TimeUtils, CloudWatchLogsError


def _to_iso8601_safe(timestamp_ms: int | None) -> str | None:
    """Unix timestamp (ms) を ISO 8601 文字列に安全に変換する。"""
    if timestamp_ms is None:
        return None
    try:
        return TimeUtils.to_iso8601(timestamp_ms)
    except Exception:
        return None


class CloudWatchLogsDescribeStreams(Tool):
    """CloudWatch Logs ログストリーム検索ツール"""
    
    logs_client: Any = None

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """指定したロググループ内のログストリームを検索・一覧取得する。"""

        try:
            # AWS 認証情報の解決
            credentials = resolve_aws_credentials(self, tool_parameters)
            if tool_parameters.get("aws_region"):
                credentials["aws_region"] = tool_parameters["aws_region"]

            # クライアントの初期化（認証情報変更時はリセット）
            reset_clients_on_credential_change(self, credentials, ["logs_client"])
            if not self.logs_client:
                client_kwargs = build_boto3_client_kwargs(credentials)
                self.logs_client = boto3.client("logs", **client_kwargs)
                
        except Exception as exc:  # pragma: no cover - boto3 初期化エラー
            error_msg = CloudWatchLogsError.format_aws_error_for_user(
                exc, "CloudWatch Logs クライアントの初期化"
            )
            yield self.create_text_message(f"AWS クライアントの初期化に失敗しました: {error_msg}")
            return

        # 必須パラメータの検証
        log_group_name = (tool_parameters.get("log_group_name") or "").strip()
        if not log_group_name:
            error_msg = CloudWatchLogsError.handle_validation_error(
                "log_group_name", "required_parameter"
            )
            yield self.create_text_message(error_msg)
            return

        # オプションパラメータの取得と検証
        log_stream_name_prefix = (tool_parameters.get("log_stream_name_prefix") or "").strip() or None
        order_by = (tool_parameters.get("order_by") or "LogStreamName").strip()
        descending = bool(tool_parameters.get("descending", False))
        next_token = (tool_parameters.get("next_token") or "").strip() or None

        # max_items の検証
        max_items_raw = tool_parameters.get("max_items", 50)
        try:
            max_items = int(max_items_raw)
            if max_items < 1 or max_items > 50:
                raise ValueError("範囲外")
        except (TypeError, ValueError):
            error_msg = CloudWatchLogsError.handle_validation_error(
                "max_items", "out_of_range", "1から50の間で指定してください"
            )
            yield self.create_text_message(error_msg)
            return

        # order_by の検証
        valid_order_by = ["LogStreamName", "LastEventTime"]
        if order_by not in valid_order_by:
            error_msg = CloudWatchLogsError.handle_validation_error(
                "order_by", "invalid_value", f"有効な値: {', '.join(valid_order_by)}"
            )
            yield self.create_text_message(error_msg)
            return

        # API リクエストパラメータの構築
        request_kwargs: dict[str, Any] = {
            "logGroupName": log_group_name,
            "orderBy": order_by,
            "descending": descending,
            "limit": max_items,
        }
        
        if log_stream_name_prefix:
            request_kwargs["logStreamNamePrefix"] = log_stream_name_prefix
        if next_token:
            request_kwargs["nextToken"] = next_token

        try:
            # CloudWatch Logs API の呼び出し
            response = self.logs_client.describe_log_streams(**request_kwargs)
            
        except ClientError as exc:
            # AWS API エラーのハンドリング
            context = CloudWatchLogsError.create_parameter_error_context(
                "ログストリーム検索",
                log_group_name=log_group_name,
                log_stream_name_prefix=log_stream_name_prefix
            )
            error_msg = CloudWatchLogsError.handle_client_error(exc, context)
            yield self.create_text_message(error_msg)
            return
            
        except Exception as exc:  # pragma: no cover - 想定外エラー
            error_msg = CloudWatchLogsError.format_aws_error_for_user(
                exc, "ログストリーム検索", log_group_name=log_group_name
            )
            yield self.create_text_message(f"ログストリーム検索に失敗しました: {error_msg}")
            return

        # レスポンスデータの処理
        log_streams: list[dict[str, Any]] = []
        for stream in response.get("logStreams", []):
            log_streams.append({
                "log_stream_name": stream.get("logStreamName"),
                "creation_time": _to_iso8601_safe(stream.get("creationTime")),
                "first_event_time": _to_iso8601_safe(stream.get("firstEventTime")),
                "last_event_time": _to_iso8601_safe(stream.get("lastEventTime")),
                "last_ingestion_time": _to_iso8601_safe(stream.get("lastIngestionTime")),
                "arn": stream.get("arn"),
                "stored_bytes": stream.get("storedBytes"),
            })

        # JSON レスポンスの構築
        payload = {
            "log_group_name": log_group_name,
            "log_stream_name_prefix": log_stream_name_prefix,
            "order_by": order_by,
            "descending": descending,
            "max_items": max_items,
            "next_token": response.get("nextToken"),
            "log_streams": log_streams,
            "stream_count": len(log_streams),
        }

        # JSON 出力
        yield self.create_json_message(payload)

        # テキスト出力（人間が読みやすい形式）
        if not log_streams:
            if log_stream_name_prefix:
                summary = f"ロググループ '{log_group_name}' でプレフィックス '{log_stream_name_prefix}' に一致するログストリームが見つかりませんでした。"
            else:
                summary = f"ロググループ '{log_group_name}' にログストリームが見つかりませんでした。"
        else:
            # サンプルのログストリーム名を表示（最大3つ）
            sample_names = [stream["log_stream_name"] for stream in log_streams[:3] if stream.get("log_stream_name")]
            sample_text = ", ".join(sample_names)
            
            if log_stream_name_prefix:
                summary = f"ロググループ '{log_group_name}' でプレフィックス '{log_stream_name_prefix}' に一致するログストリームを {len(log_streams)} 件見つけました。サンプル: {sample_text}"
            else:
                summary = f"ロググループ '{log_group_name}' でログストリームを {len(log_streams)} 件見つけました。サンプル: {sample_text}"
            
            # 継続トークンがある場合の案内
            if response.get("nextToken"):
                summary += f" (さらに結果があります。next_token を使用して続きを取得できます)"

        yield self.create_text_message(summary)