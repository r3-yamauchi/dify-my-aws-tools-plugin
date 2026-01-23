"""
場所: tools/agentcore/agentcore_memory.py
内容: Bedrock AgentCore Memory SDK を利用してメモリーリソースへ会話ログを記録/取得する Dify ツール。
目的: Workflow から AgentCore Memoryを生成し、情報の記録(record)と履歴取得(retrieve)を安全に実行できるようにする。
"""

import json
import logging
import os
import sys
from collections.abc import Generator
from typing import Any, Dict, Optional

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from utils.utils import resolve_aws_credentials

# AgentCore SDK は追加依存のため、同梱されていない場合も考慮する
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from bedrock_agentcore.memory import MemoryClient

    AGENTCORE_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - SDK 未導入環境に備える
    MemoryClient = None
    AGENTCORE_SDK_AVAILABLE = False

logger = logging.getLogger(__name__)


class AgentCoreMemoryTool(Tool):
    """AgentCore Memory の record / retrieve 操作をまとめたツール本体."""

    memory_client: Any = None
    memory_id: str | None = None
    actor_id: str | None = None
    session_id: str | None = None

    # ------------------------------------------------------------------
    # 初期化や ID 生成まわり
    # ------------------------------------------------------------------
    def _clean_id_parameter(self, value: str) -> str:
        """引用符などを除去して素の ID 文字列を返す."""
        if value and isinstance(value, str):
            trimmed = value.strip()
            if (trimmed.startswith("\"") and trimmed.endswith("\"")) or (
                trimmed.startswith("'") and trimmed.endswith("'")
            ):
                trimmed = trimmed[1:-1]
            return trimmed
        return value

    def _initialize_memory_client(self, tool_parameters: dict[str, Any]) -> bool:
        """
        AWS 資格情報から MemoryClient を構築する
        
        標準的な認証情報取得パターンを使用し、boto3の認証チェーンに委譲する
        """
        if not AGENTCORE_SDK_AVAILABLE:
            logger.error("AgentCore Memory SDK not available")
            return False

        try:
            # 標準的な認証情報解決パターンを使用
            credentials = resolve_aws_credentials(self, tool_parameters)
            aws_region = credentials.get("aws_region") or "us-east-1"

            # MemoryClient は内部で boto3 を使用するため、
            # boto3 の標準認証チェーン（環境変数、~/.aws/credentials、IAMロールなど）が自動的に使用される
            self.memory_client = MemoryClient(region_name=aws_region)
            logger.info("AgentCore Memory client initialized")
            return True
        except Exception as exc:  # pragma: no cover - SDK 例外
            logger.error(f"Failed to initialize Memory client: {exc}")
            return False

    def _create_new_memory_resource(self) -> tuple[str, str, str]:
        """メモリー・アクター・セッション ID のセットを生成する."""
        import time
        import uuid

        timestamp = int(time.time())
        memory_name = f"autoMemory_{timestamp}"
        actor_id = f"actor_{uuid.uuid4().hex[:8]}"
        session_id = f"session_{uuid.uuid4().hex[:8]}"

        default_strategies = [
            {"semanticMemoryStrategy": {"name": "semanticMemory", "namespaces": ["/semantic/{actorId}/{sessionId}"]}},
            {"summaryMemoryStrategy": {"name": "summaryMemory", "namespaces": ["/summaries/{actorId}/{sessionId}"]}},
            {"userPreferenceMemoryStrategy": {"name": "userPreferenceMemory", "namespaces": ["/userPreference/{actorId}/{sessionId}"]}},
        ]

        result = self.memory_client.create_memory_and_wait(
            name=memory_name,
            description="Auto-created memory resource",
            strategies=default_strategies,
        )
        memory_id = result.get("memoryId", "unknown")
        logger.info("Created new memory resource %s", memory_id)
        return memory_id, actor_id, session_id

    # ------------------------------------------------------------------
    # メモリー操作
    # ------------------------------------------------------------------
    def _record_information(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """information フィールドを AgentCore Memory へ記録する."""
        information = tool_parameters.get("information", "")
        if not information:
            yield self.create_text_message("Error: Information to record is required")
            return

        memory_id = self.memory_id
        actor_id = self.actor_id
        session_id = self.session_id

        if not (memory_id and actor_id and session_id):
            yield self.create_text_message("❌ Missing memory/actor/session ID")
            return

        yield self.create_text_message(f"💾 Recording information for {actor_id}...")
        try:
            messages = [(information, "USER"), ("Information recorded successfully.", "ASSISTANT")]
            result = self.memory_client.create_event(
                memory_id=memory_id,
                actor_id=actor_id,
                session_id=session_id,
                messages=messages,
            )
            event_id = "unknown"
            if isinstance(result, dict):
                event = result.get("event") or result
                event_id = event.get("eventId", event_id)

            response_text = (
                "✅ Information recorded successfully!\n\n"
                f"Event ID: {event_id}\nMemory ID: {memory_id}\nActor ID: {actor_id}\n"
                f"Session ID: {session_id}\nInformation length: {len(information)} characters"
            )
            yield self.create_text_message(response_text)
        except Exception as exc:
            logger.error("Record information error: %s", exc, exc_info=True)
            yield self.create_text_message(f"Exception in record operation: {exc}")

    def _retrieve_history(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """指定件数の会話履歴を取得し JSON で返す."""
        k = tool_parameters.get("max_results", 10)
        if not isinstance(k, int) or not (1 <= k <= 50):
            k = 10

        memory_id = self.memory_id
        actor_id = self.actor_id
        session_id = self.session_id
        if not (memory_id and actor_id and session_id):
            yield self.create_text_message("❌ Missing memory/actor/session ID")
            return

        yield self.create_text_message(
            f"📚 Retrieving last {k} conversation turns for {actor_id} (session: {session_id})"
        )
        try:
            events = self.memory_client.get_last_k_turns(
                memory_id=memory_id,
                actor_id=actor_id,
                session_id=session_id,
                k=k,
            )
            formatted_events = []
            if isinstance(events, list):
                for event in events:
                    metadata = event.get("metadata", {}) or {}
                    created_at = metadata.get("createdAt")
                    formatted_events.append(
                        {
                            "event_id": event.get("eventId"),
                            "messages": event.get("messages", []),
                            "metadata": metadata,
                            "created_at": created_at,
                        }
                    )

            response_data = {
                "message": f"Retrieved last {len(formatted_events)} conversation turns successfully",
                "data": {
                    "memory_id": memory_id,
                    "actor_id": actor_id,
                    "session_id": session_id,
                    "turns_requested": k,
                    "turns_retrieved": len(formatted_events),
                    "conversation_turns": formatted_events,
                },
            }
            yield self.create_json_message(response_data)
        except Exception as exc:
            logger.error("Retrieve history error: %s", exc, exc_info=True)
            yield self.create_text_message(f"Exception in retrieve operation: {exc}")

    # ------------------------------------------------------------------
    # Dify Tool エントリ
    # ------------------------------------------------------------------
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        """record/retrieve の要求を受け、AgentCore Memory SDK を呼び出す."""
        operation = tool_parameters.get("operation")
        if operation not in {"record", "retrieve"}:
            yield self.create_text_message("❌ Invalid operation: specify 'record' or 'retrieve'")
            return

        if not self.memory_client and not self._initialize_memory_client(tool_parameters):
            yield self.create_text_message("❌ Failed to initialize AgentCore Memory client")
            return

        # 既存 ID が渡されていれば利用、無ければ新規作成
        memory_id = self._clean_id_parameter(tool_parameters.get("memory_id", ""))
        actor_id = self._clean_id_parameter(tool_parameters.get("actor_id", ""))
        session_id = self._clean_id_parameter(tool_parameters.get("session_id", ""))

        if not (memory_id and actor_id and session_id):
            try:
                memory_id, actor_id, session_id = self._create_new_memory_resource()
                yield self.create_text_message(
                    "🏗️ Created new memory resource. Please store the IDs for future calls."
                )
                yield self.create_json_message(
                    {"memory_id": memory_id, "actor_id": actor_id, "session_id": session_id}
                )
            except Exception as exc:
                yield self.create_text_message(f"❌ Failed to create memory resource: {exc}")
                return

        self.memory_id = memory_id
        self.actor_id = actor_id
        self.session_id = session_id

        if operation == "record":
            yield from self._record_information(tool_parameters)
        else:
            yield from self._retrieve_history(tool_parameters)