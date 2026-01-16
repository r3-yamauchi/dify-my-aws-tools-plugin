"""
場所: tools/agentcore/agentcore_memory_manager.py
内容: Bedrock AgentCore Memory リソースのライフサイクル管理を行う Dify ツール。
目的: Memory リソースの作成、一覧取得、詳細確認、削除を効率的に行う。
"""

import json
import logging
import os
import sys
from collections.abc import Generator
from typing import Any, Dict, Optional, List

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from utils.utils import resolve_aws_credentials
from utils.error_handler import AgentCoreError

# AgentCore SDK は追加依存のため、同梱されていない場合も考慮する
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from bedrock_agentcore.memory import MemoryClient

    AGENTCORE_SDK_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - SDK 未導入環境に備える
    MemoryClient = None
    AGENTCORE_SDK_AVAILABLE = False
    print(f"Warning: bedrock-agentcore SDK import failed: {exc}")

logger = logging.getLogger(__name__)


class AgentCoreMemoryManagerTool(Tool):
    """Memory リソースのライフサイクル管理を行うツール本体."""

    memory_client: Any = None

    # ------------------------------------------------------------------
    # 初期化
    # ------------------------------------------------------------------
    def _initialize_memory_client(self, tool_parameters: dict[str, Any]) -> bool:
        """
        AWS 資格情報から MemoryClient を構築する。
        
        Args:
            tool_parameters: ツールパラメータ（認証情報を含む）
            
        Returns:
            bool: 初期化が成功した場合 True、失敗した場合 False
            
        要件: 2.1, 4.1, 4.2, 4.3, 4.4
        """
        if not AGENTCORE_SDK_AVAILABLE:
            logger.error("AgentCore Memory SDK not available")
            return False

        try:
            # 既存の認証情報解決機構を使用（要件 4.1）
            credentials = resolve_aws_credentials(self, tool_parameters)
            
            # デフォルトリージョンの設定（要件 4.4）
            aws_region = credentials.get("aws_region") or "us-east-1"
            aws_access_key_id = credentials.get("aws_access_key_id")
            aws_secret_access_key = credentials.get("aws_secret_access_key")

            # 明示的な AK/SK が渡された場合は環境変数経由で設定
            # ツールパラメータの認証情報を優先（要件 4.3）
            if aws_access_key_id and aws_secret_access_key:
                os.environ["AWS_ACCESS_KEY_ID"] = aws_access_key_id
                os.environ["AWS_SECRET_ACCESS_KEY"] = aws_secret_access_key
                os.environ["AWS_REGION"] = aws_region

            # MemoryClient を初期化
            self.memory_client = MemoryClient(region_name=aws_region)
            logger.info("AgentCore Memory client initialized successfully")
            return True
            
        except Exception as exc:  # pragma: no cover - SDK 例外
            logger.error(f"Failed to initialize Memory client: {exc}")
            return False

    # ------------------------------------------------------------------
    # Memory 一覧取得
    # ------------------------------------------------------------------
    def _list_memories(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        Memory リソースの一覧を取得する。
        
        Args:
            tool_parameters: ツールパラメータ（フィルター条件、ソート順、最大件数を含む）
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
            
        要件: 2.1, 2.2, 2.3, 7.6
        """
        try:
            # パラメータの取得
            filter_status = tool_parameters.get("filter_status")
            sort_by = tool_parameters.get("sort_by", "created_at")
            max_results = tool_parameters.get("max_results", 100)
            
            # 最大件数の制限（要件 7.6）
            if not isinstance(max_results, int) or max_results < 1:
                max_results = 100
            if max_results > 1000:
                max_results = 1000
            
            yield self.create_text_message(f"📋 Memory リソース一覧を取得中...")
            
            # Memory 一覧を取得（要件 2.1）
            memories = self.memory_client.list_memories(max_results=max_results)
            
            # フィルター条件の適用（要件 2.2）
            if filter_status:
                memories = [m for m in memories if m.get("status") == filter_status]
            
            # ソート順の適用（要件 2.3）
            if sort_by == "name":
                memories = sorted(memories, key=lambda m: m.get("name", ""))
            elif sort_by == "created_at":
                memories = sorted(memories, key=lambda m: m.get("createdAt", ""), reverse=True)
            elif sort_by == "updated_at":
                memories = sorted(memories, key=lambda m: m.get("updatedAt", ""), reverse=True)
            
            # レスポンスの整形
            formatted_memories = []
            for memory in memories:
                formatted_memories.append({
                    "memory_id": memory.get("memoryId"),
                    "name": memory.get("name"),
                    "status": memory.get("status"),
                    "description": memory.get("description"),
                    "created_at": memory.get("createdAt"),
                    "updated_at": memory.get("updatedAt"),
                })
            
            response_data = {
                "message": f"Memory 一覧を取得しました（{len(formatted_memories)}件）",
                "data": {
                    "total_count": len(formatted_memories),
                    "filter_status": filter_status,
                    "sort_by": sort_by,
                    "memories": formatted_memories,
                }
            }
            
            yield self.create_json_message(response_data)
            
        except Exception as exc:
            logger.error(f"List memories error: {exc}", exc_info=True)
            context = AgentCoreError.create_error_context(
                operation="list_memories",
                filter_status=filter_status,
                sort_by=sort_by
            )
            
            # ClientError の場合は適切なエラーメッセージを生成
            from botocore.exceptions import ClientError
            if isinstance(exc, ClientError):
                error_message = AgentCoreError.handle_client_error(exc, context)
            else:
                error_message = f"Memory 一覧の取得中に予期しないエラーが発生しました: {exc}"
            
            yield self.create_text_message(f"❌ {error_message}")

    # ------------------------------------------------------------------
    # Memory 詳細取得
    # ------------------------------------------------------------------
    def _get_memory_details(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        Memory リソースの詳細情報を取得する。
        
        Args:
            tool_parameters: ツールパラメータ（memory_id を含む）
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
            
        要件: 2.4
        """
        memory_id = tool_parameters.get("memory_id")
        
        if not memory_id:
            yield self.create_text_message("❌ Memory ID が指定されていません")
            return
        
        try:
            yield self.create_text_message(f"🔍 Memory 詳細情報を取得中: {memory_id}")
            
            # Memory 詳細を取得（要件 2.4）
            response = self.memory_client.gmcp_client.get_memory(memoryId=memory_id)
            memory = response.get("memory", {})
            
            # レスポンスの整形
            response_data = {
                "message": "Memory 詳細情報を取得しました",
                "data": {
                    "memory_id": memory.get("memoryId"),
                    "name": memory.get("name"),
                    "status": memory.get("status"),
                    "description": memory.get("description"),
                    "strategies": memory.get("strategies", []),
                    "event_expiry_days": memory.get("eventExpiryDays"),
                    "created_at": memory.get("createdAt"),
                    "updated_at": memory.get("updatedAt"),
                }
            }
            
            yield self.create_json_message(response_data)
            
        except Exception as exc:
            logger.error(f"Get memory details error: {exc}", exc_info=True)
            context = AgentCoreError.create_error_context(
                operation="get_memory_details",
                memory_id=memory_id
            )
            
            # ClientError の場合は適切なエラーメッセージを生成（要件 2.8）
            from botocore.exceptions import ClientError
            if isinstance(exc, ClientError):
                error_message = AgentCoreError.handle_client_error(exc, context)
            else:
                error_message = f"Memory 詳細情報の取得中に予期しないエラーが発生しました: {exc}"
            
            yield self.create_text_message(f"❌ {error_message}")

    # ------------------------------------------------------------------
    # Memory 作成
    # ------------------------------------------------------------------
    def _create_memory(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        Memory リソースを作成する。
        
        Args:
            tool_parameters: ツールパラメータ（name, description, strategies を含む）
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
            
        要件: 2.6, 2.7, 2.9
        """
        name = tool_parameters.get("name")
        description = tool_parameters.get("description", "")
        strategies = tool_parameters.get("strategies")
        
        if not name:
            yield self.create_text_message("❌ Memory 名が指定されていません")
            return
        
        try:
            # デフォルト戦略の設定（要件 2.7）
            if not strategies:
                strategies = [
                    {"semanticMemoryStrategy": {"name": "semanticMemory", "namespaces": ["/semantic/{actorId}/{sessionId}"]}},
                    {"summaryMemoryStrategy": {"name": "summaryMemory", "namespaces": ["/summaries/{actorId}/{sessionId}"]}},
                    {"userPreferenceMemoryStrategy": {"name": "userPreferenceMemory", "namespaces": ["/userPreference/{actorId}/{sessionId}"]}},
                ]
            
            yield self.create_text_message(f"🏗️ Memory を作成中: {name}")
            
            # Memory を作成（要件 2.6）
            result = self.memory_client.create_memory_and_wait(
                name=name,
                description=description,
                strategies=strategies,
            )
            
            memory_id = result.get("memoryId", "unknown")
            status = result.get("status", "unknown")
            
            response_text = (
                f"✅ Memory を作成しました\n\n"
                f"Memory ID: {memory_id}\n"
                f"名前: {name}\n"
                f"ステータス: {status}\n"
                f"説明: {description if description else '(なし)'}"
            )
            
            yield self.create_text_message(response_text)
            yield self.create_json_message({
                "memory_id": memory_id,
                "name": name,
                "status": status,
                "description": description
            })
            
        except Exception as exc:
            logger.error(f"Create memory error: {exc}", exc_info=True)
            context = AgentCoreError.create_error_context(
                operation="create_memory",
                name=name
            )
            
            # ClientError の場合は適切なエラーメッセージを生成（要件 2.9）
            from botocore.exceptions import ClientError
            if isinstance(exc, ClientError):
                error_message = AgentCoreError.handle_client_error(exc, context)
            else:
                error_message = f"Memory の作成中に予期しないエラーが発生しました: {exc}"
            
            yield self.create_text_message(f"❌ {error_message}")

    # ------------------------------------------------------------------
    # Memory 削除
    # ------------------------------------------------------------------
    def _delete_memory(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        Memory リソースを削除する。
        
        Args:
            tool_parameters: ツールパラメータ（memory_id を含む）
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
            
        要件: 2.5, 2.8
        """
        memory_id = tool_parameters.get("memory_id")
        
        if not memory_id:
            yield self.create_text_message("❌ Memory ID が指定されていません")
            return
        
        try:
            yield self.create_text_message(f"🗑️ Memory を削除中: {memory_id}")
            
            # Memory を削除（要件 2.5）
            self.memory_client.delete_memory_and_wait(memory_id=memory_id)
            
            response_text = (
                f"✅ Memory を削除しました\n\n"
                f"Memory ID: {memory_id}"
            )
            
            yield self.create_text_message(response_text)
            
        except Exception as exc:
            logger.error(f"Delete memory error: {exc}", exc_info=True)
            context = AgentCoreError.create_error_context(
                operation="delete_memory",
                memory_id=memory_id
            )
            
            # ClientError の場合は適切なエラーメッセージを生成（要件 2.8）
            from botocore.exceptions import ClientError
            if isinstance(exc, ClientError):
                error_message = AgentCoreError.handle_client_error(exc, context)
            else:
                error_message = f"Memory の削除中に予期しないエラーが発生しました: {exc}"
            
            yield self.create_text_message(f"❌ {error_message}")

    # ------------------------------------------------------------------
    # Dify Tool エントリ
    # ------------------------------------------------------------------
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        """
        Memory リソース管理操作のメインエントリポイント。
        
        Args:
            tool_parameters: ツールパラメータ（operation を含む）
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
            
        要件: 5.3, 5.4
        """
        operation = tool_parameters.get("operation")
        
        # 操作の検証
        valid_operations = {"list", "get", "create", "delete"}
        if operation not in valid_operations:
            yield self.create_text_message(
                f"❌ 無効な操作です: {operation}\n"
                f"有効な操作: {', '.join(valid_operations)}"
            )
            return
        
        # Memory クライアントの初期化
        if not self.memory_client and not self._initialize_memory_client(tool_parameters):
            yield self.create_text_message("❌ AgentCore Memory クライアントの初期化に失敗しました")
            return
        
        # 操作のルーティング
        if operation == "list":
            yield from self._list_memories(tool_parameters)
        elif operation == "get":
            yield from self._get_memory_details(tool_parameters)
        elif operation == "create":
            yield from self._create_memory(tool_parameters)
        elif operation == "delete":
            yield from self._delete_memory(tool_parameters)
