"""
場所: tools/agentcore/agentcore_memory_search.py
内容: AgentCore Memory からベクトル検索 API を呼び出し、指定メモリー/ネームスペースに保存された情報を取得するツール。
目的: AgentCore Memory に蓄積した会話やナレッジを Workflow から検索可能にする。
"""

import json
import logging
from collections.abc import Generator
from typing import Any, Dict
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from utils.utils import resolve_aws_credentials

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from bedrock_agentcore.memory import MemoryClient
    AGENTCORE_SDK_AVAILABLE = True
except ImportError as exc:  # pragma: no cover
    MemoryClient = None
    AGENTCORE_SDK_AVAILABLE = False
    print(f"Warning: bedrock-agentcore SDK import failed: {exc}")

logger = logging.getLogger(__name__)


class AgentCoreMemorySearchTool(Tool):
    memory_client: Any = None
    
    def _clean_id_parameter(self, value: str) -> str:
        """ID 文字列の前後にある引用符を取り除く"""
        if value and isinstance(value, str):
            # 前後の引用符を削除する
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
        return value
    
    def _initialize_memory_client(self, tool_parameters: dict[str, Any]) -> bool:
        """AWS 資格情報を元に MemoryClient を初期化する."""
        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            aws_region = credentials.get("aws_region") or 'us-east-1'
            aws_access_key_id = credentials.get("aws_access_key_id")
            aws_secret_access_key = credentials.get("aws_secret_access_key")

            if AGENTCORE_SDK_AVAILABLE:
                # AK/SK が両方ある場合は環境変数経由で渡す
                if aws_access_key_id and aws_secret_access_key:
                    # MemoryClient では環境変数または boto3 セッションを使用する必要がある
                    import os
                    os.environ['AWS_ACCESS_KEY_ID'] = aws_access_key_id
                    os.environ['AWS_SECRET_ACCESS_KEY'] = aws_secret_access_key
                    os.environ['AWS_REGION'] = aws_region
                
                # MemoryClient を生成
                self.memory_client = MemoryClient(region_name=aws_region)
                logger.info(f"Memory client initialized for region: {aws_region}")
                return True
            else:
                logger.error("AgentCore Memory SDK not available")
                return False
                
        except Exception as e:
            logger.error(f"Failed to initialize Memory client: {str(e)}")
            return False
    
    def _search_memories(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        """AgentCore Memory の retrieve_memories API を叩いて検索する."""
        try:
            # 業務パラメータを取り出す
            search_query = tool_parameters.get('search_query', 'all')
            max_results = tool_parameters.get('max_results', 10)
            memory_id = self._clean_id_parameter(tool_parameters.get('memory_id', ''))
            namespace = tool_parameters.get('namespace', '/')
            
            # クエリ未指定なら all を利用
            if not search_query or search_query.strip() == '':
                search_query = 'all'
            
            # ネームスペースが無ければ全戦略共通の '/'
            if not namespace or namespace.strip() == '':
                namespace = '/'
            
            if not memory_id:
                yield self.create_text_message("Error: Memory ID is required for search operation")
                return
            
            # max_results の上限をチェック
            if max_results < 1 or max_results > 20:
                max_results = 10
            
            yield self.create_text_message(f"🔍 Searching memories for: '{search_query}' in namespace: '{namespace}'")
            
            if self.memory_client:
                # retrieve_memories API を呼び出し
                result = self.memory_client.retrieve_memories(
                    memory_id=memory_id,
                    query=search_query,
                    namespace=namespace,
                    top_k=max_results
                )
                
                # レスポンスからメモリー配列を取得
                memories_list = result.get('memories', []) if isinstance(result, dict) else result
                
                # イテラブルでなければリスト化
                if not isinstance(memories_list, list):
                    memories_list = list(memories_list) if hasattr(memories_list, '__iter__') else []
                
                # 取得数を max_results で制限
                if max_results and len(memories_list) > max_results:
                    memories_list = memories_list[:max_results]
                
                # JSON シリアライズしやすい形へ変換
                processed_memories = []
                for memory in memories_list:
                    if isinstance(memory, dict):
                        # datetime オブジェクトなら ISO8601 文字列へ変換
                        processed_memory = {}
                        for key, value in memory.items():
                            if hasattr(value, 'isoformat'):  # datetime オブジェクト
                                processed_memory[key] = value.isoformat()
                            else:
                                processed_memory[key] = value
                        processed_memories.append(processed_memory)
                    else:
                        processed_memories.append(str(memory))
                
                # 詳細を付けた JSON レスポンスを組み立て
                response_data = {
                    'success': True,
                    'message': f"Found {len(processed_memories)} relevant memor(ies)",
                    'data': {
                        'memories_count': len(processed_memories),
                        'memory_id': memory_id,
                        'namespace': namespace,
                        'query': search_query,
                        'memories': processed_memories
                    }
                }
                
                # Dify が扱いやすい JSON メッセージとして返す
                yield self.create_json_message(response_data)
            else:
                yield self.create_text_message("❌ AgentCore Memory SDK not available")
                
        except Exception as e:
            logger.error(f"Search memories error: {str(e)}")
            yield self.create_text_message(f"Exception in search operation: {str(e)}")

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        """検索専用ツールとして初期化と検索処理を実行する"""
        try:
            # Memory クライアントが初期化されていない場合は初期化する
            if not self.memory_client:
                if not self._initialize_memory_client(tool_parameters):
                    yield self.create_text_message("❌ Failed to initialize AgentCore Memory client")
                    return

            # このツールは検索操作のみを実行する
            yield from self._search_memories(tool_parameters)

        except Exception as e:
            logger.error(f"Invoke error: {str(e)}", exc_info=True)
            yield self.create_text_message(f"❌ Internal error: {str(e)}")