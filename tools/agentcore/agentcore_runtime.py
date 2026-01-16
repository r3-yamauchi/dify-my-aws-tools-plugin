"""
場所: tools/agentcore/agentcore_runtime.py
内容: Amazon Bedrock AgentCore Runtime の呼び出しとステータス管理を行う Dify ツール。
目的: Runtime の同期/非同期呼び出しと実行ステータスの取得を提供する。
"""

import logging
from collections.abc import Generator
from typing import Any, Dict, Optional

import boto3
from botocore.exceptions import ClientError
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from utils.error_handler import AgentCoreError
from utils.utils import resolve_aws_credentials, build_boto3_client_kwargs

logger = logging.getLogger(__name__)


class AgentCoreRuntimeTool(Tool):
    """AgentCore Runtime の呼び出しとステータス管理を行うツール本体."""

    runtime_client: Any = None

    def _initialize_runtime_client(self, tool_parameters: Dict[str, Any]) -> bool:
        """
        boto3 bedrock-agent-runtime クライアントを初期化する
        
        Args:
            tool_parameters: ツールパラメータ辞書
            
        Returns:
            初期化が成功した場合は True、失敗した場合は False
        """
        try:
            # 認証情報を解決
            credentials = resolve_aws_credentials(self, tool_parameters)
            
            # boto3 クライアント引数を構築
            client_kwargs = build_boto3_client_kwargs(credentials)
            
            # デフォルトリージョンを設定（要件 4.4）
            if 'region_name' not in client_kwargs:
                client_kwargs['region_name'] = 'us-east-1'
            
            # bedrock-agent-runtime クライアントを作成
            self.runtime_client = boto3.client('bedrock-agent-runtime', **client_kwargs)
            
            logger.info("AgentCore Runtime client initialized successfully")
            return True
            
        except Exception as exc:
            logger.error(f"Failed to initialize Runtime client: {exc}", exc_info=True)
            return False

    def _invoke(self, tool_parameters: Dict[str, Any]) -> Generator[ToolInvokeMessage]:
        """
        メインエントリポイント - Runtime 操作を実行する
        
        Args:
            tool_parameters: ツールパラメータ辞書
            
        Yields:
            ToolInvokeMessage: 実行結果メッセージ
        """
        # 操作タイプを取得
        operation = tool_parameters.get("operation")
        
        # 有効な操作かチェック
        valid_operations = {"sync_invoke", "async_invoke", "get_status"}
        if operation not in valid_operations:
            yield self.create_text_message(
                f"❌ 無効な操作です。'sync_invoke'、'async_invoke'、または 'get_status' を指定してください。"
            )
            return
        
        # Runtime クライアントを初期化
        if not self.runtime_client and not self._initialize_runtime_client(tool_parameters):
            yield self.create_text_message("❌ AgentCore Runtime クライアントの初期化に失敗しました")
            return
        
        # 操作に応じて適切なメソッドを呼び出し
        if operation == "sync_invoke":
            yield from self._invoke_runtime_sync(tool_parameters)
        elif operation == "async_invoke":
            yield from self._invoke_runtime_async(tool_parameters)
        elif operation == "get_status":
            yield from self._get_invocation_status(tool_parameters)

    def _invoke_runtime_sync(self, tool_parameters: Dict[str, Any]) -> Generator[ToolInvokeMessage]:
        """
        Runtime を同期呼び出しする（要件 1.1）
        
        Args:
            tool_parameters: ツールパラメータ辞書
            
        Yields:
            ToolInvokeMessage: 実行結果メッセージ
        """
        runtime_id = tool_parameters.get("runtime_id")
        message = tool_parameters.get("message")
        session_id = tool_parameters.get("session_id")
        
        # 必須パラメータのチェック
        if not runtime_id:
            yield self.create_text_message("❌ Runtime ID が指定されていません")
            return
        
        if not message:
            yield self.create_text_message("❌ メッセージが指定されていません")
            return
        
        # セッション ID が指定されていない場合は新規生成（要件 1.6）
        if not session_id:
            import uuid
            session_id = f"session_{uuid.uuid4().hex[:16]}"
            logger.info(f"Generated new session ID: {session_id}")
        
        yield self.create_text_message(f"🚀 Runtime を同期呼び出し中... (Runtime ID: {runtime_id})")
        
        try:
            # Runtime を同期呼び出し
            response = self.runtime_client.invoke_agent(
                agentId=runtime_id,
                agentAliasId='TSTALIASID',
                sessionId=session_id,
                inputText=message,
                enableTrace=False
            )
            
            # レスポンスからテキストを抽出
            response_text = ""
            if 'completion' in response:
                # EventStream からレスポンスを取得
                for event in response['completion']:
                    if 'chunk' in event:
                        chunk = event['chunk']
                        if 'bytes' in chunk:
                            response_text += chunk['bytes'].decode('utf-8')
            
            # 成功メッセージを返す
            result_message = (
                f"✅ Runtime の同期呼び出しが成功しました\n\n"
                f"Session ID: {session_id}\n"
                f"レスポンス: {response_text if response_text else '(レスポンスなし)'}"
            )
            
            yield self.create_text_message(result_message)
            
            # JSON 形式でも返す
            yield self.create_json_message({
                "session_id": session_id,
                "response_text": response_text,
                "runtime_id": runtime_id
            })
            
        except ClientError as e:
            # AWS API エラーを処理（要件 4.6）
            context = AgentCoreError.create_error_context(
                operation="sync_invoke",
                runtime_id=runtime_id
            )
            error_message = AgentCoreError.handle_client_error(e, context)
            yield self.create_text_message(f"❌ {error_message}")
            
        except Exception as exc:
            logger.error(f"Unexpected error in sync_invoke: {exc}", exc_info=True)
            yield self.create_text_message(f"❌ 予期しないエラーが発生しました: {exc}")

    def _invoke_runtime_async(self, tool_parameters: Dict[str, Any]) -> Generator[ToolInvokeMessage]:
        """
        Runtime を非同期呼び出しする（要件 1.2, 1.3）
        
        Args:
            tool_parameters: ツールパラメータ辞書
            
        Yields:
            ToolInvokeMessage: 実行結果メッセージ
        """
        runtime_id = tool_parameters.get("runtime_id")
        message = tool_parameters.get("message")
        session_id = tool_parameters.get("session_id")
        
        # 必須パラメータのチェック
        if not runtime_id:
            yield self.create_text_message("❌ Runtime ID が指定されていません")
            return
        
        if not message:
            yield self.create_text_message("❌ メッセージが指定されていません")
            return
        
        # セッション ID が指定されていない場合は新規生成（要件 1.6）
        if not session_id:
            import uuid
            session_id = f"session_{uuid.uuid4().hex[:16]}"
            logger.info(f"Generated new session ID: {session_id}")
        
        yield self.create_text_message(f"🚀 Runtime を非同期呼び出し中... (Runtime ID: {runtime_id})")
        
        try:
            # Runtime を非同期呼び出し
            response = self.runtime_client.invoke_agent_async(
                agentId=runtime_id,
                agentAliasId='TSTALIASID',
                sessionId=session_id,
                inputText=message
            )
            
            # 実行 ID を取得
            invocation_id = response.get('invocationId', 'unknown')
            
            # 成功メッセージを返す
            result_message = (
                f"✅ Runtime の非同期呼び出しが開始されました\n\n"
                f"実行 ID: {invocation_id}\n"
                f"Session ID: {session_id}\n"
                f"Runtime ID: {runtime_id}\n\n"
                f"ステータスを確認するには、'get_status' 操作を使用してください。"
            )
            
            yield self.create_text_message(result_message)
            
            # JSON 形式でも返す
            yield self.create_json_message({
                "invocation_id": invocation_id,
                "session_id": session_id,
                "runtime_id": runtime_id
            })
            
        except ClientError as e:
            # AWS API エラーを処理（要件 4.6）
            context = AgentCoreError.create_error_context(
                operation="async_invoke",
                runtime_id=runtime_id
            )
            error_message = AgentCoreError.handle_client_error(e, context)
            yield self.create_text_message(f"❌ {error_message}")
            
        except Exception as exc:
            logger.error(f"Unexpected error in async_invoke: {exc}", exc_info=True)
            yield self.create_text_message(f"❌ 予期しないエラーが発生しました: {exc}")

    def _get_invocation_status(self, tool_parameters: Dict[str, Any]) -> Generator[ToolInvokeMessage]:
        """
        非同期実行のステータスを取得する（要件 1.4）
        
        Args:
            tool_parameters: ツールパラメータ辞書
            
        Yields:
            ToolInvokeMessage: 実行結果メッセージ
        """
        runtime_id = tool_parameters.get("runtime_id")
        invocation_id = tool_parameters.get("invocation_id")
        
        # 必須パラメータのチェック
        if not runtime_id:
            yield self.create_text_message("❌ Runtime ID が指定されていません")
            return
        
        if not invocation_id:
            yield self.create_text_message("❌ 実行 ID が指定されていません")
            return
        
        yield self.create_text_message(f"🔍 実行ステータスを取得中... (実行 ID: {invocation_id})")
        
        try:
            # 実行ステータスを取得
            response = self.runtime_client.get_agent_invocation(
                agentId=runtime_id,
                invocationId=invocation_id
            )
            
            # ステータス情報を抽出
            status = response.get('status', 'UNKNOWN')
            session_id = response.get('sessionId', 'unknown')
            response_text = response.get('output', {}).get('text', '')
            
            # ステータスに応じたメッセージを作成
            status_emoji = {
                'PENDING': '⏳',
                'IN_PROGRESS': '🔄',
                'COMPLETED': '✅',
                'FAILED': '❌'
            }.get(status, '❓')
            
            result_message = (
                f"{status_emoji} 実行ステータス: {status}\n\n"
                f"実行 ID: {invocation_id}\n"
                f"Session ID: {session_id}\n"
                f"Runtime ID: {runtime_id}"
            )
            
            # 完了している場合はレスポンスも表示
            if status == 'COMPLETED' and response_text:
                result_message += f"\n\nレスポンス: {response_text}"
            
            yield self.create_text_message(result_message)
            
            # JSON 形式でも返す
            yield self.create_json_message({
                "invocation_id": invocation_id,
                "session_id": session_id,
                "runtime_id": runtime_id,
                "status": status,
                "response_text": response_text if status == 'COMPLETED' else None
            })
            
        except ClientError as e:
            # AWS API エラーを処理（要件 4.6）
            context = AgentCoreError.create_error_context(
                operation="get_status",
                runtime_id=runtime_id
            )
            error_message = AgentCoreError.handle_client_error(e, context)
            yield self.create_text_message(f"❌ {error_message}")
            
        except Exception as exc:
            logger.error(f"Unexpected error in get_status: {exc}", exc_info=True)
            yield self.create_text_message(f"❌ 予期しないエラーが発生しました: {exc}")
