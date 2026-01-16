"""
場所: tools/agentcore/agentcore_observability.py
内容: AgentCore Observability のデータを統合的に取得・分析する Dify ツール。
目的: CloudWatch メトリクス、ログ、X-Ray トレースを統合し、パフォーマンス分析とデバッグを効率化する。
"""

from __future__ import annotations

import time
from collections.abc import Generator
from typing import Any, Dict, List, Optional

import boto3
from botocore.exceptions import ClientError

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

# 相対インポートとフルパスインポートの両方に対応
try:
    from utils.utils import (
        build_boto3_client_kwargs,
        resolve_aws_credentials,
        reset_clients_on_credential_change,
    )
    from utils.time_utils import TimeUtils
    from utils.error_handler import CloudWatchLogsError, AgentCoreError
except ModuleNotFoundError:  # pragma: no cover
    from my_aws_tools.utils.utils import (
        build_boto3_client_kwargs,
        resolve_aws_credentials,
        reset_clients_on_credential_change,
    )
    from my_aws_tools.utils.time_utils import TimeUtils
    from my_aws_tools.utils.error_handler import CloudWatchLogsError, AgentCoreError


class AgentCoreObservabilityTool(Tool):
    """AgentCore Observability のデータを取得・分析するツール"""
    
    logs_client: Any = None
    cloudwatch_client: Any = None
    xray_client: Any = None
    _client_credentials_signature: Any = None

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        ツールのメインエントリポイント
        
        Args:
            tool_parameters: ツールパラメータ（operation、リソース情報、認証情報を含む）
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
            
        要件: 4.1, 4.2, 4.3, 4.4
        """
        # AWS認証とクライアント初期化を実行
        auth_result = self._initialize_aws_clients(tool_parameters)
        if auth_result is not None:
            yield self.create_text_message(auth_result)
            return

        # operation パラメータの取得と検証
        operation = tool_parameters.get("operation", "").strip()
        if not operation:
            yield self.create_text_message("❌ operation パラメータが必要です")
            return

        # 操作に応じて適切なメソッドを呼び出し
        try:
            if operation == "get_metrics":
                yield from self._get_metrics(tool_parameters)
            elif operation == "get_logs":
                yield from self._get_logs(tool_parameters)
            elif operation == "get_traces":
                yield from self._get_traces(tool_parameters)
            elif operation == "analyze_performance":
                yield from self._analyze_performance(tool_parameters)
            else:
                yield self.create_text_message(
                    f"❌ 無効な operation です: {operation}\n"
                    f"有効な operation: get_metrics, get_logs, get_traces, analyze_performance"
                )
                
        except Exception as exc:
            # 包括的なエラーハンドリング
            if isinstance(exc, ClientError):
                # AWS API エラー
                context = AgentCoreError.create_error_context(
                    operation=operation,
                    resource_type=tool_parameters.get("resource_type", "不明"),
                    resource_id=tool_parameters.get("resource_id", "不明")
                )
                error_msg = AgentCoreError.handle_client_error(exc, context)
            else:
                # その他のエラー
                error_msg = AgentCoreError.format_aws_error_for_user(
                    exc, f"AgentCore Observability 操作: {operation}"
                )
            
            yield self.create_text_message(f"❌ 操作に失敗しました: {error_msg}")
            return

    def _initialize_aws_clients(self, tool_parameters: dict[str, Any]) -> Optional[str]:
        """
        AWS 認証情報の解決とクライアント初期化
        
        Args:
            tool_parameters: ツールパラメータ（認証情報を含む）
            
        Returns:
            Optional[str]: エラーメッセージ（成功時は None）
            
        要件: 9.1, 9.2, 9.3, 9.5
        """
        try:
            # AWS 認証情報の解決（要件 9.1, 9.2, 9.3）
            credentials = resolve_aws_credentials(self, tool_parameters)
            
            # リージョンの設定（パラメータで指定された場合は上書き）
            if tool_parameters.get("aws_region"):
                credentials["aws_region"] = tool_parameters["aws_region"]
            
            # デフォルトリージョンの設定（要件 9.5）
            if not credentials.get("aws_region"):
                credentials["aws_region"] = "us-east-1"
            
            # 認証情報が変更された場合はクライアントをリセット
            reset_clients_on_credential_change(
                self, 
                credentials, 
                ["logs_client", "cloudwatch_client", "xray_client"],
                "_client_credentials_signature"
            )
            
            # クライアントが未初期化または無効化された場合は新規作成
            client_kwargs = build_boto3_client_kwargs(credentials)
            
            if not self.logs_client:
                self.logs_client = boto3.client("logs", **client_kwargs)
                
            if not self.cloudwatch_client:
                self.cloudwatch_client = boto3.client("cloudwatch", **client_kwargs)
                
            if not self.xray_client:
                self.xray_client = boto3.client("xray", **client_kwargs)
                
            # 接続テストを実行（軽量なAPI呼び出し）
            try:
                # CloudWatch Logs の接続テスト
                self.logs_client.describe_log_groups(limit=1)
            except ClientError as test_exc:
                # 認証エラーや権限エラーの場合は適切にハンドリング
                error_code = test_exc.response.get('Error', {}).get('Code', '')
                if error_code in ['UnauthorizedOperation', 'AccessDeniedException', 'InvalidUserID.NotFound']:
                    context = AgentCoreError.create_error_context(
                        operation="AWS認証テスト"
                    )
                    return AgentCoreError.handle_client_error(test_exc, context)
                # その他のエラーは警告として扱い、処理を継続
                pass
                
        except ClientError as exc:
            # AWS API関連のエラー（要件 9.4）
            context = AgentCoreError.create_error_context(
                operation="AWS クライアントの初期化"
            )
            return AgentCoreError.handle_client_error(exc, context)
            
        except Exception as exc:
            # その他の予期しないエラー
            return AgentCoreError.format_aws_error_for_user(
                exc, "AWS クライアントの初期化"
            )
        
        # 初期化成功
        return None

    def _get_metrics(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        CloudWatch メトリクスを取得
        
        Args:
            tool_parameters: ツールパラメータ（リソースタイプ、リソースID、時間範囲を含む）
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
            
        要件: 4.1, 5.1, 5.2, 5.3, 5.4, 12.2
        """
        # パラメータ検証
        resource_type = tool_parameters.get("resource_type", "").strip()
        resource_id = tool_parameters.get("resource_id", "").strip()
        time_range = tool_parameters.get("time_range", "1h").strip()
        
        if not resource_type:
            yield self.create_text_message("❌ resource_type パラメータが必要です")
            return
            
        if not resource_id:
            yield self.create_text_message("❌ resource_id パラメータが必要です")
            return
        
        # リソースタイプの検証（要件 4.5）
        valid_resource_types = ["runtime", "memory", "code_interpreter"]
        if resource_type not in valid_resource_types:
            yield self.create_text_message(
                f"❌ 無効な resource_type です: {resource_type}\n"
                f"有効な resource_type: {', '.join(valid_resource_types)}"
            )
            return
        
        yield self.create_text_message(
            f"🔍 メトリクス取得を開始します...\n"
            f"リソースタイプ: {resource_type}\n"
            f"リソースID: {resource_id}\n"
            f"時間範囲: {time_range}"
        )
        
        # TODO: メトリクス取得の実装（タスク 8.1）
        yield self.create_text_message("⚠️ メトリクス取得機能は実装中です")

    def _get_logs(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        CloudWatch Logs を取得
        
        Args:
            tool_parameters: ツールパラメータ（リソースタイプ、リソースID、時間範囲を含む）
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
            
        要件: 4.2, 6.2, 6.3, 6.4, 6.5, 12.3
        """
        # パラメータ検証
        resource_type = tool_parameters.get("resource_type", "").strip()
        resource_id = tool_parameters.get("resource_id", "").strip()
        time_range = tool_parameters.get("time_range", "1h").strip()
        
        if not resource_type:
            yield self.create_text_message("❌ resource_type パラメータが必要です")
            return
            
        if not resource_id:
            yield self.create_text_message("❌ resource_id パラメータが必要です")
            return
        
        yield self.create_text_message(
            f"🔍 ログ取得を開始します...\n"
            f"リソースタイプ: {resource_type}\n"
            f"リソースID: {resource_id}\n"
            f"時間範囲: {time_range}"
        )
        
        # TODO: ログ取得の実装（タスク 9.1）
        yield self.create_text_message("⚠️ ログ取得機能は実装中です")

    def _get_traces(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        X-Ray トレースを取得
        
        Args:
            tool_parameters: ツールパラメータ（Session ID、時間範囲を含む）
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
            
        要件: 4.3, 7.2, 7.3, 7.4, 12.4
        """
        # パラメータ検証
        session_id = tool_parameters.get("session_id", "").strip()
        time_range = tool_parameters.get("time_range", "1h").strip()
        
        if not session_id:
            yield self.create_text_message("❌ session_id パラメータが必要です")
            return
        
        yield self.create_text_message(
            f"🔍 トレース取得を開始します...\n"
            f"Session ID: {session_id}\n"
            f"時間範囲: {time_range}"
        )
        
        # TODO: トレース取得の実装（タスク 10.1）
        yield self.create_text_message("⚠️ トレース取得機能は実装中です")

    def _analyze_performance(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        パフォーマンスを分析
        
        Args:
            tool_parameters: ツールパラメータ（リソースタイプ、リソースID、時間範囲、分析タイプを含む）
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
            
        要件: 4.4, 8.1, 8.2, 8.3, 8.4
        """
        # パラメータ検証
        resource_type = tool_parameters.get("resource_type", "").strip()
        resource_id = tool_parameters.get("resource_id", "").strip()
        time_range = tool_parameters.get("time_range", "24h").strip()
        analysis_type = tool_parameters.get("analysis_type", "bottleneck").strip()
        
        if not resource_type:
            yield self.create_text_message("❌ resource_type パラメータが必要です")
            return
            
        if not resource_id:
            yield self.create_text_message("❌ resource_id パラメータが必要です")
            return
        
        # 分析タイプの検証
        valid_analysis_types = ["bottleneck", "error_analysis", "cost_analysis"]
        if analysis_type not in valid_analysis_types:
            yield self.create_text_message(
                f"❌ 無効な analysis_type です: {analysis_type}\n"
                f"有効な analysis_type: {', '.join(valid_analysis_types)}"
            )
            return
        
        yield self.create_text_message(
            f"🔍 パフォーマンス分析を開始します...\n"
            f"リソースタイプ: {resource_type}\n"
            f"リソースID: {resource_id}\n"
            f"時間範囲: {time_range}\n"
            f"分析タイプ: {analysis_type}"
        )
        
        # TODO: パフォーマンス分析の実装（タスク 11.1）
        yield self.create_text_message("⚠️ パフォーマンス分析機能は実装中です")

    def _build_metric_query(self, resource_type: str, resource_id: str, metrics: List[str]) -> List[Dict]:
        """
        CloudWatch メトリクスクエリを構築
        
        Args:
            resource_type: リソースタイプ（runtime, memory, code_interpreter）
            resource_id: リソースID
            metrics: 取得するメトリクスのリスト
            
        Returns:
            List[Dict]: CloudWatch メトリクスクエリのリスト
            
        要件: 5.1, 5.2, 5.3
        """
        # TODO: メトリクスクエリ構築の実装（タスク 8.1）
        return []

    def _execute_logs_insights_query(
        self, 
        log_group: str, 
        query: str, 
        start_time: int, 
        end_time: int
    ) -> List[Dict]:
        """
        CloudWatch Logs Insights クエリを実行
        
        Args:
            log_group: ロググループ名
            query: Logs Insights クエリ文字列
            start_time: 開始時刻（Unix タイムスタンプ、秒単位）
            end_time: 終了時刻（Unix タイムスタンプ、秒単位）
            
        Returns:
            List[Dict]: ログエントリのリスト
            
        要件: 6.1, 11.1
        """
        # TODO: ログクエリ実行の実装（タスク 9.1）
        # 既存の cwlogs_insight.py のロジックを再利用
        return []

    def _fetch_xray_traces(
        self, 
        filter_expression: str, 
        start_time: int, 
        end_time: int
    ) -> List[Dict]:
        """
        X-Ray トレースを取得
        
        Args:
            filter_expression: X-Ray フィルター式
            start_time: 開始時刻（Unix タイムスタンプ、秒単位）
            end_time: 終了時刻（Unix タイムスタンプ、秒単位）
            
        Returns:
            List[Dict]: トレースデータのリスト
            
        要件: 7.1
        """
        # TODO: トレース取得の実装（タスク 10.1）
        return []

    def _analyze_bottlenecks(self, traces: List[Dict]) -> Dict[str, Any]:
        """
        ボトルネックを分析
        
        Args:
            traces: トレースデータのリスト
            
        Returns:
            Dict[str, Any]: ボトルネック分析結果
            
        要件: 8.1
        """
        # TODO: ボトルネック分析の実装（タスク 11.1）
        return {}

    def _analyze_errors(self, logs: List[Dict], traces: List[Dict]) -> Dict[str, Any]:
        """
        エラーを分析
        
        Args:
            logs: ログエントリのリスト
            traces: トレースデータのリスト
            
        Returns:
            Dict[str, Any]: エラー分析結果
            
        要件: 8.2
        """
        # TODO: エラー分析の実装（タスク 11.1）
        return {}
