"""
場所: tools/cloudwatch_logs_get_events.py
内容: CloudWatch Logs の指定したログストリームから全ログイベントを取得するツール。
目的: 特定のログストリームの完全なログ履歴を取得し、双方向ページネーションで大量データに対応する。
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


class CloudWatchLogsGetEvents(Tool):
    """CloudWatch Logs ログストリーム内容取得ツール"""
    
    logs_client: Any = None

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """指定したログストリームから全ログイベントを取得する。"""

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

        log_stream_name = (tool_parameters.get("log_stream_name") or "").strip()
        if not log_stream_name:
            error_msg = CloudWatchLogsError.handle_validation_error(
                "log_stream_name", "required_parameter"
            )
            yield self.create_text_message(error_msg)
            return

        # オプションパラメータの取得
        start_from_head = bool(tool_parameters.get("start_from_head", True))
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
        max_events_raw = tool_parameters.get("max_events", 10000)
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

        # API リクエストパラメータの構築
        request_kwargs: dict[str, Any] = {
            "logGroupName": log_group_name,
            "logStreamName": log_stream_name,
            "startFromHead": start_from_head,
            "limit": max_events,
        }
        
        if start_time_ms is not None:
            request_kwargs["startTime"] = start_time_ms
        if end_time_ms is not None:
            request_kwargs["endTime"] = end_time_ms
        if next_token:
            request_kwargs["nextToken"] = next_token

        try:
            # CloudWatch Logs API の呼び出し
            response = self.logs_client.get_log_events(**request_kwargs)
            
        except ClientError as exc:
            # AWS API エラーのハンドリング
            context = CloudWatchLogsError.create_parameter_error_context(
                "ログストリーム内容取得",
                log_group_name=log_group_name,
                log_stream_name=log_stream_name
            )
            error_msg = CloudWatchLogsError.handle_client_error(exc, context)
            yield self.create_text_message(error_msg)
            return
            
        except Exception as exc:  # pragma: no cover - 想定外エラー
            error_msg = CloudWatchLogsError.format_aws_error_for_user(
                exc, "ログストリーム内容取得", 
                log_group_name=log_group_name, 
                log_stream_name=log_stream_name
            )
            yield self.create_text_message(f"ログストリーム内容取得に失敗しました: {error_msg}")
            return

        # レスポンスデータの処理
        events: list[dict[str, Any]] = []
        for event in response.get("events", []):
            events.append({
                "timestamp": _to_iso8601_safe(event.get("timestamp")),
                "ingestion_time": _to_iso8601_safe(event.get("ingestionTime")),
                "message": event.get("message"),
            })

        # JSON レスポンスの構築
        payload = {
            "log_group_name": log_group_name,
            "log_stream_name": log_stream_name,
            "start_time": _to_iso8601_safe(start_time_ms),
            "end_time": _to_iso8601_safe(end_time_ms),
            "start_from_head": start_from_head,
            "max_events": max_events,
            "next_forward_token": response.get("nextForwardToken"),
            "next_backward_token": response.get("nextBackwardToken"),
            "events": events,
            "event_count": len(events),
        }

        # JSON 出力
        yield self.create_json_message(payload)

        # テキスト出力（人間が読みやすい形式）
        if not events:
            # 検索条件の要約
            conditions = []
            if start_time_ms or end_time_ms:
                time_range = []
                if start_time_ms:
                    time_range.append(f"開始: {_to_iso8601_safe(start_time_ms)}")
                if end_time_ms:
                    time_range.append(f"終了: {_to_iso8601_safe(end_time_ms)}")
                conditions.append(f"時間範囲 ({', '.join(time_range)})")
            
            condition_text = f" ({', '.join(conditions)})" if conditions else ""
            summary = f"ログストリーム '{log_stream_name}' (ロググループ '{log_group_name}') でログイベントが見つかりませんでした{condition_text}。"
        else:
            # 検索結果の要約
            direction_text = "先頭から" if start_from_head else "末尾から"
            summary = f"ログストリーム '{log_stream_name}' (ロググループ '{log_group_name}') から{direction_text}ログイベントを {len(events):,} 件取得しました。"
            
            # 時間範囲の情報
            if start_time_ms or end_time_ms:
                if start_time_ms and end_time_ms:
                    duration = TimeUtils.format_duration(start_time_ms, end_time_ms)
                    summary += f" 期間: {duration}"
                elif start_time_ms:
                    summary += f" ({_to_iso8601_safe(start_time_ms)} 以降)"
                elif end_time_ms:
                    summary += f" ({_to_iso8601_safe(end_time_ms)} 以前)"
            
            # 継続トークンの情報
            tokens = []
            if response.get("nextForwardToken"):
                tokens.append("前方継続")
            if response.get("nextBackwardToken"):
                tokens.append("後方継続")
            
            if tokens:
                summary += f" (継続取得可能: {', '.join(tokens)})"
            
            # イベントの時間範囲情報
            if events:
                first_event_time = events[0].get("timestamp")
                last_event_time = events[-1].get("timestamp")
                if first_event_time and last_event_time and first_event_time != last_event_time:
                    summary += f" イベント時間範囲: {first_event_time} ～ {last_event_time}"

        yield self.create_text_message(summary)