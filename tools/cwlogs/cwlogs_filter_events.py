"""
場所: tools/cloudwatch_logs_filter_events.py
内容: CloudWatch Logs の指定した条件でログイベントを検索・取得するツール。
目的: 時間範囲やフィルターパターンを使用してログイベントを効率的に検索し、デバッグや調査に必要な情報を提供する。
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any, Optional

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


class CloudWatchLogsFilterEvents(Tool):
    """CloudWatch Logs ログイベント検索ツール"""
    
    logs_client: Any = None

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """指定した条件でログイベントを検索・取得する。"""

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
                
        except Exception as exc:  # pragma: no cover - boto3 初期化エラーは稀
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

        # オプションパラメータの取得
        log_stream_names = tool_parameters.get("log_stream_names", [])
        if isinstance(log_stream_names, str):
            # 文字列の場合はカンマ区切りで分割
            log_stream_names = [name.strip() for name in log_stream_names.split(",") if name.strip()]
        elif not isinstance(log_stream_names, list):
            log_stream_names = []

        log_stream_name_prefix = (tool_parameters.get("log_stream_name_prefix") or "").strip() or None
        filter_pattern = (tool_parameters.get("filter_pattern") or "").strip() or None
        next_token = (tool_parameters.get("next_token") or "").strip() or None

        # 時刻パラメータの解析
        start_time_input = tool_parameters.get("start_time")
        end_time_input = tool_parameters.get("end_time")
        
        try:
            start_time_ms = TimeUtils.parse_time_input(start_time_input) if start_time_input else None
            end_time_ms = TimeUtils.parse_time_input(end_time_input) if end_time_input else None
            
            # 時刻範囲の妥当性検証
            valid, error_message = TimeUtils.validate_time_range(start_time_ms, end_time_ms)
            if not valid:
                yield self.create_text_message(f"時刻範囲エラー: {error_message}")
                return
                
        except ValueError as e:
            yield self.create_text_message(f"時刻解析エラー: {str(e)}")
            return

        # max_events の検証
        max_events_raw = tool_parameters.get("max_events", 1000)
        try:
            max_events = int(max_events_raw)
            if max_events < 1 or max_events > 10000:
                raise ValueError("範囲外")
        except (TypeError, ValueError):
            error_msg = CloudWatchLogsError.handle_validation_error(
                "max_events", "out_of_range", "1から10000の間で指定してください"
            )
            yield self.create_text_message(error_msg)
            return

        # log_stream_names と log_stream_name_prefix の競合チェック
        if log_stream_names and log_stream_name_prefix:
            error_msg = CloudWatchLogsError.handle_validation_error(
                "log_stream_names/log_stream_name_prefix", "conflicting_parameters",
                "log_stream_names と log_stream_name_prefix は同時に指定できません"
            )
            yield self.create_text_message(error_msg)
            return

        # API リクエストパラメータの構築
        request_kwargs: dict[str, Any] = {
            "logGroupName": log_group_name,
            "limit": max_events,
        }
        
        if log_stream_names:
            request_kwargs["logStreamNames"] = log_stream_names
        if log_stream_name_prefix:
            request_kwargs["logStreamNamePrefix"] = log_stream_name_prefix
        if filter_pattern:
            request_kwargs["filterPattern"] = filter_pattern
        if start_time_ms is not None:
            request_kwargs["startTime"] = start_time_ms
        if end_time_ms is not None:
            request_kwargs["endTime"] = end_time_ms
        if next_token:
            request_kwargs["nextToken"] = next_token

        try:
            # CloudWatch Logs API の呼び出し
            response = self.logs_client.filter_log_events(**request_kwargs)
            
        except ClientError as exc:
            # AWS API エラーのハンドリング
            context = CloudWatchLogsError.create_parameter_error_context(
                "ログイベント検索",
                log_group_name=log_group_name,
                filter_pattern=filter_pattern
            )
            error_msg = CloudWatchLogsError.handle_client_error(exc, context)
            yield self.create_text_message(error_msg)
            return
            
        except Exception as exc:  # pragma: no cover - 想定外のエラー
            error_msg = CloudWatchLogsError.format_aws_error_for_user(
                exc, "ログイベント検索", log_group_name=log_group_name
            )
            yield self.create_text_message(f"ログイベント検索に失敗しました: {error_msg}")
            return

        # レスポンスデータの処理
        events: list[dict[str, Any]] = []
        for event in response.get("events", []):
            events.append({
                "event_id": event.get("eventId"),
                "timestamp": _to_iso8601_safe(event.get("timestamp")),
                "ingestion_time": _to_iso8601_safe(event.get("ingestionTime")),
                "message": event.get("message"),
                "log_stream_name": event.get("logStreamName"),
            })

        # 検索されたログストリーム情報の処理
        searched_log_streams: list[dict[str, Any]] = []
        for stream_info in response.get("searchedLogStreams", []):
            searched_log_streams.append({
                "log_stream_name": stream_info.get("logStreamName"),
                "searched_completely": stream_info.get("searchedCompletely", False),
            })

        # JSON レスポンスの構築
        payload = {
            "log_group_name": log_group_name,
            "log_stream_names": log_stream_names if log_stream_names else None,
            "log_stream_name_prefix": log_stream_name_prefix,
            "filter_pattern": filter_pattern,
            "start_time": _to_iso8601_safe(start_time_ms),
            "end_time": _to_iso8601_safe(end_time_ms),
            "max_events": max_events,
            "next_token": response.get("nextToken"),
            "events": events,
            "searched_log_streams": searched_log_streams,
            "event_count": len(events),
        }

        # JSON 出力
        yield self.create_json_message(payload)

        # テキスト出力（人間が読みやすい形式）
        if not events:
            # 検索条件の要約
            conditions = []
            if filter_pattern:
                conditions.append(f"フィルターパターン '{filter_pattern}'")
            if start_time_ms or end_time_ms:
                time_range = []
                if start_time_ms:
                    time_range.append(f"開始: {_to_iso8601_safe(start_time_ms)}")
                if end_time_ms:
                    time_range.append(f"終了: {_to_iso8601_safe(end_time_ms)}")
                conditions.append(f"時間範囲 ({', '.join(time_range)})")
            
            condition_text = f" ({', '.join(conditions)})" if conditions else ""
            summary = f"ロググループ '{log_group_name}' で条件に一致するログイベントが見つかりませんでした{condition_text}。"
        else:
            # 検索結果の要約
            conditions = []
            if filter_pattern:
                conditions.append(f"フィルター '{filter_pattern}'")
            if start_time_ms or end_time_ms:
                if start_time_ms and end_time_ms:
                    duration = TimeUtils.format_duration(start_time_ms, end_time_ms)
                    conditions.append(f"期間 {duration}")
                elif start_time_ms:
                    conditions.append(f"{_to_iso8601_safe(start_time_ms)} 以降")
                elif end_time_ms:
                    conditions.append(f"{_to_iso8601_safe(end_time_ms)} 以前")
            
            condition_text = f" ({', '.join(conditions)})" if conditions else ""
            summary = f"ロググループ '{log_group_name}' でログイベントを {len(events)} 件見つけました{condition_text}。"
            
            # 検索されたログストリーム数の情報
            if searched_log_streams:
                stream_count = len(searched_log_streams)
                complete_count = sum(1 for s in searched_log_streams if s.get("searched_completely"))
                summary += f" 検索対象ログストリーム: {stream_count} 件 (完全検索: {complete_count} 件)"
            
            # 継続トークンがある場合の案内
            if response.get("nextToken"):
                summary += f" (さらに結果があります。next_token を使用して続きを取得できます)"

        yield self.create_text_message(summary)