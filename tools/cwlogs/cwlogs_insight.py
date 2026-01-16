"""
場所: tools/cwlogs/cwlogs_insight.py
内容: CloudWatch Logs Insightを使用したログ検索・分析ツール。
目的: CloudWatch Logs Insightクエリ言語を使用して高度なログ分析を実行し、構造化された結果を提供する。
"""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import Any, Dict, List, Optional

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


class CloudWatchLogsInsight(Tool):
    """CloudWatch Logs Insight クエリ実行ツール"""
    
    logs_client: Any = None
    _client_credentials_signature: Any = None

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """CloudWatch Logs Insightクエリを実行し、結果を返す"""

        # AWS認証とクライアント初期化を実行
        auth_result = self._initialize_aws_client(tool_parameters)
        if auth_result is not None:
            yield self.create_text_message(auth_result)
            return

        # パラメータ検証を実行
        validation_result = self._validate_parameters(tool_parameters)
        if validation_result is not None:
            yield self.create_text_message(validation_result)
            return

        # 検証済みパラメータの取得
        validated_params = self._extract_validated_parameters(tool_parameters)
        
        try:
            # CloudWatch Logs Insightクエリを実行
            query_result = self._execute_query(validated_params)
            
            # 結果を生成して返す
            yield from self._generate_output(query_result)
            
        except Exception as exc:
            # 包括的なエラーハンドリング
            if isinstance(exc, ClientError):
                # AWS API エラー
                context = CloudWatchLogsError.create_parameter_error_context(
                    "CloudWatch Logs Insightクエリ実行",
                    log_group_names=", ".join(validated_params.get("log_group_names", [])) if validated_params else "不明",
                    query_string=(validated_params.get("query_string", "")[:50] + "...") if validated_params and len(validated_params.get("query_string", "")) > 50 else validated_params.get("query_string", "") if validated_params else "不明"
                )
                error_msg = CloudWatchLogsError.handle_client_error(exc, context)
            else:
                # その他のエラー
                error_msg = CloudWatchLogsError.format_aws_error_for_user(
                    exc, "CloudWatch Logs Insightクエリ実行"
                )
            
            yield self.create_text_message(f"クエリ実行に失敗しました: {error_msg}")
            return

    def _initialize_aws_client(self, tool_parameters: dict[str, Any]) -> Optional[str]:
        """AWS認証情報の解決とクライアント初期化"""
        
        try:
            # AWS 認証情報の解決
            credentials = resolve_aws_credentials(self, tool_parameters)
            
            # リージョンの設定（パラメータで指定された場合は上書き）
            if tool_parameters.get("aws_region"):
                credentials["aws_region"] = tool_parameters["aws_region"]
            
            # 認証情報が変更された場合はクライアントをリセット
            reset_clients_on_credential_change(
                self, 
                credentials, 
                ["logs_client"],
                "_client_credentials_signature"
            )
            
            # クライアントが未初期化または無効化された場合は新規作成
            if not self.logs_client:
                client_kwargs = build_boto3_client_kwargs(credentials)
                self.logs_client = boto3.client("logs", **client_kwargs)
                
                # 接続テストを実行（軽量なAPI呼び出し）
                try:
                    # describe_log_groups を制限付きで呼び出してクライアントをテスト
                    self.logs_client.describe_log_groups(limit=1)
                except ClientError as test_exc:
                    # 認証エラーや権限エラーの場合は適切にハンドリング
                    error_code = test_exc.response.get('Error', {}).get('Code', '')
                    if error_code in ['UnauthorizedOperation', 'AccessDeniedException', 'InvalidUserID.NotFound']:
                        context = CloudWatchLogsError.create_parameter_error_context(
                            "AWS認証テスト"
                        )
                        return CloudWatchLogsError.handle_client_error(test_exc, context)
                    # その他のエラーは警告として扱い、処理を継続
                    pass
                
        except ClientError as exc:
            # AWS API関連のエラー
            context = CloudWatchLogsError.create_parameter_error_context(
                "CloudWatch Logs クライアントの初期化"
            )
            return CloudWatchLogsError.handle_client_error(exc, context)
            
        except Exception as exc:
            # その他の予期しないエラー
            return CloudWatchLogsError.format_aws_error_for_user(
                exc, "CloudWatch Logs クライアントの初期化"
            )
        
        # 初期化成功
        return None

    def _validate_parameters(self, tool_parameters: dict[str, Any]) -> Optional[str]:
        """入力パラメータの検証を実行"""
        
        # 必須パラメータ: log_group_names の検証
        log_group_names = (tool_parameters.get("log_group_names") or "").strip()
        if not log_group_names:
            return CloudWatchLogsError.handle_validation_error(
                "log_group_names", "required_parameter"
            )
        
        # 必須パラメータ: query_string の検証
        query_string = (tool_parameters.get("query_string") or "").strip()
        if not query_string:
            return CloudWatchLogsError.handle_validation_error(
                "query_string", "required_parameter"
            )
        
        # 時刻パラメータの解析と検証
        start_time_input = tool_parameters.get("start_time")
        end_time_input = tool_parameters.get("end_time")
        
        try:
            start_time_ms = TimeUtils.parse_time_input(start_time_input) if start_time_input else None
            end_time_ms = TimeUtils.parse_time_input(end_time_input) if end_time_input else None
            
            # 時刻範囲の妥当性検証
            valid, error_message = TimeUtils.validate_time_range(start_time_ms, end_time_ms)
            if not valid:
                return f"時刻範囲エラー: {error_message}"
                
        except ValueError as e:
            return f"時刻解析エラー: {str(e)}"
        
        # max_results の検証
        max_results_raw = tool_parameters.get("max_results", 1000)
        try:
            # 文字列の場合は無効として扱う（数値文字列も含む）
            if isinstance(max_results_raw, str):
                raise ValueError("文字列は無効")
            # float('inf')や非数値の場合を適切に処理
            if isinstance(max_results_raw, float) and (max_results_raw == float('inf') or max_results_raw != max_results_raw):
                raise ValueError("無効な数値")
            max_results = int(max_results_raw)
            if max_results < 1 or max_results > 10000:
                raise ValueError("範囲外")
        except (TypeError, ValueError, OverflowError):
            return CloudWatchLogsError.handle_validation_error(
                "max_results", "out_of_range", "1から10000の間で指定してください"
            )
        
        # すべての検証が成功
        return None

    def _extract_validated_parameters(self, tool_parameters: dict[str, Any]) -> Dict[str, Any]:
        """検証済みパラメータを抽出"""
        
        # ロググループ名の処理（カンマ区切りで分割）
        log_group_names_str = (tool_parameters.get("log_group_names") or "").strip()
        log_group_names = [name.strip() for name in log_group_names_str.split(",") if name.strip()]
        
        # クエリ文字列の処理
        query_string = (tool_parameters.get("query_string") or "").strip()
        
        # 時刻パラメータの処理
        start_time_input = tool_parameters.get("start_time")
        end_time_input = tool_parameters.get("end_time")
        
        start_time_ms = TimeUtils.parse_time_input(start_time_input) if start_time_input else None
        end_time_ms = TimeUtils.parse_time_input(end_time_input) if end_time_input else None
        
        # max_results の処理
        max_results = int(tool_parameters.get("max_results", 1000))
        
        return {
            "log_group_names": log_group_names,
            "query_string": query_string,
            "start_time_ms": start_time_ms,
            "end_time_ms": end_time_ms,
            "max_results": max_results
        }

    def _execute_query(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """CloudWatch Logs Insightクエリを実行"""
        
        try:
            # start_query APIのパラメータを構築
            start_query_params = {
                "logGroupNames": params["log_group_names"],
                "queryString": params["query_string"],
                "limit": params["max_results"]
            }
            
            # 時間範囲の設定
            if params["start_time_ms"] is not None:
                start_query_params["startTime"] = params["start_time_ms"] // 1000  # 秒単位に変換
            if params["end_time_ms"] is not None:
                start_query_params["endTime"] = params["end_time_ms"] // 1000  # 秒単位に変換
            
            # CloudWatch Logs Insight クエリを開始
            start_response = self.logs_client.start_query(**start_query_params)
            query_id = start_response["queryId"]
            
            # クエリ完了を待機
            query_result = self._wait_for_completion(query_id)
            
            # 結果を構造化して返す
            formatted_result = self._format_results(query_result, params)
            
            return {
                "query_id": query_id,
                "status": formatted_result.get("status", "Complete"),
                "log_group_names": params["log_group_names"],
                "query_string": params["query_string"],
                "start_time_ms": params["start_time_ms"],
                "end_time_ms": params["end_time_ms"],
                "max_results": params["max_results"],
                "results": formatted_result.get("results", []),
                "statistics": formatted_result.get("statistics", {}),
                "execution_time_ms": formatted_result.get("statistics", {}).get("executionTimeInMillis", 0),
                "scanned_bytes": formatted_result.get("statistics", {}).get("bytesScanned", 0),
                "records_matched": formatted_result.get("statistics", {}).get("recordsMatched", 0),
                "records_scanned": formatted_result.get("statistics", {}).get("recordsScanned", 0)
            }
            
        except ClientError as exc:
            # AWS API エラーのハンドリング
            context = CloudWatchLogsError.create_parameter_error_context(
                "CloudWatch Logs Insightクエリ開始",
                log_group_name=", ".join(params["log_group_names"]),
                query_string=params["query_string"][:100] + "..." if len(params["query_string"]) > 100 else params["query_string"]
            )
            error_msg = CloudWatchLogsError.handle_client_error(exc, context)
            raise Exception(error_msg)
            
        except Exception as exc:
            # その他のエラー
            error_msg = CloudWatchLogsError.format_aws_error_for_user(
                exc, "CloudWatch Logs Insightクエリ開始",
                log_group_names=", ".join(params["log_group_names"]),
                query_string=params["query_string"][:50] + "..." if len(params["query_string"]) > 50 else params["query_string"]
            )
            raise Exception(error_msg)

    def _wait_for_completion(self, query_id: str, timeout_seconds: int = 300) -> Dict[str, Any]:
        """
        ポーリング方式でクエリ完了を待機
        
        Args:
            query_id: クエリID
            timeout_seconds: タイムアウト時間（デフォルト5分）
        
        Returns:
            クエリ結果
        """
        start_time = time.time()
        poll_interval = 2  # 2秒間隔でポーリング
        
        while time.time() - start_time < timeout_seconds:
            try:
                response = self.logs_client.get_query_results(queryId=query_id)
                status = response.get('status')
                
                if status == 'Complete':
                    return response
                elif status == 'Failed':
                    error_message = response.get('error', 'Unknown error')
                    raise Exception(f"クエリが失敗しました: {error_message}")
                elif status == 'Cancelled':
                    raise Exception("クエリがキャンセルされました")
                elif status == 'Timeout':
                    raise Exception("クエリがタイムアウトしました")
                
                # Running または Scheduled の場合は待機
                time.sleep(poll_interval)
                
            except ClientError as exc:
                # get_query_results APIのエラー
                context = CloudWatchLogsError.create_parameter_error_context(
                    "クエリ結果取得",
                    query_id=query_id,
                    operation="get_query_results"
                )
                error_msg = CloudWatchLogsError.handle_client_error(exc, context)
                raise Exception(error_msg)
        
        # タイムアウト時はクエリを停止
        try:
            self.logs_client.stop_query(queryId=query_id)
        except Exception:
            # stop_query が失敗しても継続
            pass
        
        raise Exception(f"クエリがタイムアウトしました（{timeout_seconds}秒）")

    def _format_results(self, query_result: Dict[str, Any], params: Dict[str, Any]) -> Dict[str, Any]:
        """
        クエリ結果の構造化処理
        
        Args:
            query_result: CloudWatch Logs Insightからの生の結果
            params: クエリパラメータ
            
        Returns:
            構造化された結果
        """
        # 生の結果データを取得
        raw_results = query_result.get("results", [])
        statistics = query_result.get("statistics", {})
        
        # 結果の構造化処理
        formatted_results = []
        for result_row in raw_results:
            formatted_row = {}
            
            # CloudWatch Logs Insight の結果は2つの形式をサポート:
            # 1. フィールドオブジェクトの配列: [{'field': '@timestamp', 'value': '...'}, ...]
            # 2. 辞書形式: {'@timestamp': '...', '@message': '...'}
            
            if isinstance(result_row, list):
                # フィールドオブジェクトの配列形式
                for field in result_row:
                    if isinstance(field, dict) and "field" in field and "value" in field:
                        field_name = field.get("field", "")
                        field_value = field.get("value", "")
                        
                        # タイムスタンプフィールドの処理
                        if field_name == "@timestamp" and field_value:
                            try:
                                # ISO 8601形式のタイムスタンプを人間が読みやすい形式に変換
                                formatted_row[field_name] = self._format_timestamp(field_value)
                                formatted_row[f"{field_name}_raw"] = field_value  # 元の値も保持
                            except Exception:
                                # 変換に失敗した場合は元の値をそのまま使用
                                formatted_row[field_name] = field_value
                        else:
                            # その他のフィールドはそのまま設定
                            formatted_row[field_name] = field_value
            elif isinstance(result_row, dict):
                # 辞書形式（テスト用モックデータなど）
                for field_name, field_value in result_row.items():
                    # タイムスタンプフィールドの処理
                    if field_name == "@timestamp" and field_value:
                        try:
                            # ISO 8601形式のタイムスタンプを人間が読みやすい形式に変換
                            formatted_row[field_name] = self._format_timestamp(field_value)
                            formatted_row[f"{field_name}_raw"] = field_value  # 元の値も保持
                        except Exception:
                            # 変換に失敗した場合は元の値をそのまま使用
                            formatted_row[field_name] = field_value
                    else:
                        # その他のフィールドはそのまま設定
                        formatted_row[field_name] = field_value
            
            # フィールドのラベル付け
            formatted_row = self._add_field_labels(formatted_row)
            formatted_results.append(formatted_row)
        
        # 時間範囲フィルタリングを適用
        time_filtered_results = self._validate_result_time_range(formatted_results, params.get("start_time_ms"), params.get("end_time_ms"))
        
        # 結果数制限を適用
        final_results, was_limited = self._apply_result_limit(time_filtered_results, params.get("max_results", 1000))
        
        return {
            "status": query_result.get("status", "Complete"),
            "results": final_results,
            "statistics": statistics,
            "was_limited": was_limited,
            "original_count": len(formatted_results),
            "time_filtered_count": len(time_filtered_results),
            "final_count": len(final_results)
        }

    def _format_timestamp(self, timestamp_str: str) -> str:
        """
        タイムスタンプを人間が読みやすい形式に変換
        
        Args:
            timestamp_str: ISO 8601形式のタイムスタンプ文字列
            
        Returns:
            フォーマットされたタイムスタンプ文字列
        """
        try:
            from datetime import datetime, timezone, timedelta
            
            # ISO 8601形式をパース
            if timestamp_str.endswith('Z'):
                dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            else:
                dt = datetime.fromisoformat(timestamp_str)
            
            # 日本時間に変換して表示（JST = UTC+9）
            jst = timezone(timedelta(hours=9))
            dt_jst = dt.astimezone(jst)
            
            # 人間が読みやすい形式でフォーマット
            return dt_jst.strftime("%Y-%m-%d %H:%M:%S JST")
            
        except Exception:
            # パースに失敗した場合はUTCで表示を試行
            try:
                from datetime import datetime
                if timestamp_str.endswith('Z'):
                    dt = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
                else:
                    dt = datetime.fromisoformat(timestamp_str)
                return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
            except Exception:
                # 最終的に失敗した場合は元の文字列を返す
                return timestamp_str

    def _format_timestamp_from_ms(self, timestamp_ms: int) -> str:
        """
        ミリ秒タイムスタンプを人間が読みやすい形式に変換
        
        Args:
            timestamp_ms: ミリ秒単位のタイムスタンプ
            
        Returns:
            フォーマットされたタイムスタンプ文字列
        """
        try:
            from datetime import datetime, timezone, timedelta
            
            # ミリ秒を秒に変換してdatetimeオブジェクトを作成
            dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
            
            # 日本時間に変換して表示（JST = UTC+9）
            jst = timezone(timedelta(hours=9))
            dt_jst = dt.astimezone(jst)
            
            # 人間が読みやすい形式でフォーマット
            return dt_jst.strftime("%Y-%m-%d %H:%M:%S JST")
            
        except Exception:
            # 変換に失敗した場合はUTCで表示を試行
            try:
                from datetime import datetime, timezone
                dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
                return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
            except Exception:
                # 最終的に失敗した場合はミリ秒をそのまま返す
                return str(timestamp_ms)

    def _apply_result_limit(self, results: List[Dict[str, Any]], max_results: int) -> tuple[List[Dict[str, Any]], bool]:
        """
        結果数制限の適用
        
        Args:
            results: クエリ結果のリスト
            max_results: 最大結果件数
            
        Returns:
            制限適用後の結果リストと制限が適用されたかのフラグ
        """
        if len(results) <= max_results:
            # 結果数が制限以下の場合はそのまま返す
            return results, False
        
        # 結果数が制限を超えている場合は制限を適用
        limited_results = results[:max_results]
        return limited_results, True

    def _validate_result_time_range(self, results: List[Dict[str, Any]], start_time_ms: Optional[int], end_time_ms: Optional[int]) -> List[Dict[str, Any]]:
        """
        結果の時間範囲内検証とフィルタリング
        
        Args:
            results: クエリ結果のリスト
            start_time_ms: 開始時刻（ミリ秒）
            end_time_ms: 終了時刻（ミリ秒）
            
        Returns:
            時間範囲内にフィルタリングされた結果
        """
        if not start_time_ms and not end_time_ms:
            # 時間範囲が指定されていない場合はそのまま返す
            return results
        
        filtered_results = []
        
        for result in results:
            # @timestampフィールドから時刻を取得
            timestamp_raw = result.get("@timestamp_raw")
            if not timestamp_raw:
                # タイムスタンプがない場合はスキップ
                continue
            
            try:
                # ISO 8601形式のタイムスタンプをミリ秒に変換
                result_time_ms = TimeUtils.parse_time_input(timestamp_raw)
                
                # 時間範囲内かチェック
                in_range = True
                if start_time_ms and result_time_ms < start_time_ms:
                    in_range = False
                if end_time_ms and result_time_ms > end_time_ms:
                    in_range = False
                
                if in_range:
                    filtered_results.append(result)
                    
            except (ValueError, TypeError):
                # タイムスタンプの解析に失敗した場合は含める（安全側に倒す）
                filtered_results.append(result)
        
        return filtered_results

    def _add_field_labels(self, result_row: Dict[str, Any]) -> Dict[str, Any]:
        """
        結果フィールドにラベルを追加
        
        Args:
            result_row: 結果行の辞書
            
        Returns:
            ラベル付きの結果行
        """
        # 一般的なCloudWatch Logsフィールドのラベルマッピング
        field_labels = {
            "@timestamp": "タイムスタンプ",
            "@message": "メッセージ",
            "@logStream": "ログストリーム",
            "@log": "ログソース",
            "@requestId": "リクエストID",
            "@duration": "実行時間",
            "@billedDuration": "課金時間",
            "@memorySize": "メモリサイズ",
            "@maxMemoryUsed": "最大メモリ使用量"
        }
        
        # ラベル情報を追加（元のデータは保持）
        labeled_row = result_row.copy()
        
        # メタデータとしてラベル情報を追加
        labels = {}
        for field_name, field_value in result_row.items():
            if field_name in field_labels:
                labels[field_name] = field_labels[field_name]
            elif field_name.endswith("_raw"):
                # _rawサフィックスのフィールドはラベル付けしない
                continue
            else:
                # カスタムフィールドはそのまま
                labels[field_name] = field_name
        
        # ラベル情報をメタデータとして追加
        if labels:
            labeled_row["_field_labels"] = labels
        
        return labeled_row

    def _generate_output(self, query_result: Dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """JSON出力とテキスト出力を生成"""
        
        # JSON出力: 完全な結果データ
        yield self.create_json_message(query_result)
        
        # テキスト出力: ヒット件数と実行統計の要約
        result_count = len(query_result.get('results', []))
        log_groups = ', '.join(f"'{lg}'" for lg in query_result.get('log_group_names', []))
        execution_time = query_result.get('execution_time_ms', 0) / 1000
        scanned_bytes = query_result.get('scanned_bytes', 0)
        scanned_mb = scanned_bytes / (1024 * 1024) if scanned_bytes > 0 else 0
        
        # 時間範囲情報の追加
        start_time_ms = query_result.get('start_time_ms')
        end_time_ms = query_result.get('end_time_ms')
        time_range_info = ""
        
        if start_time_ms or end_time_ms:
            time_parts = []
            if start_time_ms:
                start_time_str = self._format_timestamp_from_ms(start_time_ms)
                time_parts.append(f"開始: {start_time_str}")
            if end_time_ms:
                end_time_str = self._format_timestamp_from_ms(end_time_ms)
                time_parts.append(f"終了: {end_time_str}")
            time_range_info = f"\n時間範囲: {', '.join(time_parts)}"
        
        # 結果制限情報の追加
        was_limited = query_result.get('was_limited', False)
        max_results = query_result.get('max_results', 1000)
        limit_info = ""
        
        if was_limited:
            original_count = query_result.get('original_count', 0)
            limit_info = f"\n注意: 結果が{max_results}件に制限されました（元の結果: {original_count}件）"
        
        if result_count == 0:
            summary = f"CloudWatch Logs Insightクエリが完了しました。\n"
            summary += f"ロググループ {log_groups} で条件に一致するログエントリは見つかりませんでした。{time_range_info}\n"
            summary += f"実行時間: {execution_time:.1f}秒、スキャンしたデータ: {scanned_mb:.1f} MB"
        else:
            summary = f"CloudWatch Logs Insightクエリが完了しました。\n"
            summary += f"ロググループ {log_groups} で {result_count} 件のログエントリが見つかりました。{time_range_info}{limit_info}\n"
            summary += f"実行時間: {execution_time:.1f}秒、スキャンしたデータ: {scanned_mb:.1f} MB"
        
        yield self.create_text_message(summary)