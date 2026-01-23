"""
場所: tools/agentcore/agentcore_memory_search_advanced.py
内容: AgentCore Memory Search の高度な機能を提供するツール
目的: フィルター、ソート、ページネーション、ハイライト機能を提供

要件: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 13.7, 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7, 16.1, 16.2, 16.3, 16.4, 16.5, 16.6
"""

import json
import logging
import os
import re
from collections.abc import Generator
from datetime import datetime
from typing import Any, Dict, List, Optional
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from utils.utils import resolve_aws_credentials
except ModuleNotFoundError:  # pragma: no cover
    from my_aws_tools.utils.utils import resolve_aws_credentials

try:
    from bedrock_agentcore.memory import MemoryClient
    AGENTCORE_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    MemoryClient = None
    AGENTCORE_SDK_AVAILABLE = False

logger = logging.getLogger(__name__)

# ページサイズの最大値
MAX_PAGE_SIZE = 100


class AgentCoreMemorySearchAdvancedTool(Tool):
    """Memory Search の高度な機能を提供するツール"""
    
    memory_client: Any = None

    def _clean_id_parameter(self, value: str) -> str:
        """ID 文字列の前後にある引用符を取り除く"""
        if value and isinstance(value, str):
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
        return value
    
    def _initialize_memory_client(self, tool_parameters: Dict[str, Any]) -> bool:
        """
        MemoryClient を初期化する
        
        標準的な認証情報取得パターンを使用し、boto3の認証チェーンに委譲する
        
        Args:
            tool_parameters: ツールパラメータ
            
        Returns:
            bool: 初期化が成功した場合 True
            
        要件: 17.1, 17.2, 17.3, 17.4
        """
        try:
            # 標準的な認証情報解決パターンを使用
            credentials = resolve_aws_credentials(self, tool_parameters)
            aws_region = credentials.get("aws_region") or 'us-east-1'

            if AGENTCORE_SDK_AVAILABLE:
                # MemoryClient は内部で boto3 を使用するため、
                # boto3 の標準認証チェーン（環境変数、~/.aws/credentials、IAMロールなど）が自動的に使用される
                self.memory_client = MemoryClient(region_name=aws_region)
                logger.info(f"Memory client initialized for region: {aws_region}")
            else:
                logger.error("AgentCore Memory SDK not available")
                return False
            
            return True
                
        except Exception as e:
            logger.error(f"Failed to initialize memory client: {str(e)}")
            return False
    
    def _parse_datetime(self, datetime_str: str) -> Optional[datetime]:
        """
        日時文字列をパースする
        
        Args:
            datetime_str: 日時文字列（ISO 8601形式）
            
        Returns:
            Optional[datetime]: パースされた datetime オブジェクト
        """
        try:
            return datetime.fromisoformat(datetime_str.replace('Z', '+00:00'))
        except Exception as e:
            logger.warning(f"Failed to parse datetime: {datetime_str}, error: {str(e)}")
            return None
    
    def _apply_time_range_filter(
        self,
        events: List[Any],
        start_time: Optional[str] = None,
        end_time: Optional[str] = None
    ) -> List[Any]:
        """
        時間範囲フィルターを適用する
        
        Args:
            events: イベントのリスト
            start_time: 開始時刻（ISO 8601形式）
            end_time: 終了時刻（ISO 8601形式）
            
        Returns:
            List[Any]: フィルタリングされたイベントのリスト
            
        要件: 13.1, 13.6, 13.7
        """
        if not start_time and not end_time:
            return events
        
        start_dt = self._parse_datetime(start_time) if start_time else None
        end_dt = self._parse_datetime(end_time) if end_time else None
        
        filtered = []
        for event in events:
            # イベントのタイムスタンプを取得
            timestamp_str = None
            if isinstance(event, dict):
                timestamp_str = event.get('timestamp') or event.get('created_at')
            
            if not timestamp_str:
                continue
            
            event_dt = self._parse_datetime(timestamp_str)
            if not event_dt:
                continue
            
            # 時間範囲チェック
            if start_dt and event_dt < start_dt:
                continue
            if end_dt and event_dt > end_dt:
                continue
            
            filtered.append(event)
        
        logger.info(f"Time range filter applied: {len(events)} -> {len(filtered)}")
        return filtered
    
    def _apply_actor_filter(
        self,
        events: List[Any],
        actor_ids: List[str]
    ) -> List[Any]:
        """
        Actor ID フィルターを適用する
        
        Args:
            events: イベントのリスト
            actor_ids: Actor ID のリスト
            
        Returns:
            List[Any]: フィルタリングされたイベントのリスト
            
        要件: 13.2
        """
        if not actor_ids:
            return events
        
        filtered = [
            event for event in events
            if isinstance(event, dict) and event.get('actor_id') in actor_ids
        ]
        
        logger.info(f"Actor filter applied: {len(events)} -> {len(filtered)}")
        return filtered
    
    def _apply_session_filter(
        self,
        events: List[Any],
        session_ids: List[str]
    ) -> List[Any]:
        """
        Session ID フィルターを適用する
        
        Args:
            events: イベントのリスト
            session_ids: Session ID のリスト
            
        Returns:
            List[Any]: フィルタリングされたイベントのリスト
            
        要件: 13.3
        """
        if not session_ids:
            return events
        
        filtered = [
            event for event in events
            if isinstance(event, dict) and event.get('session_id') in session_ids
        ]
        
        logger.info(f"Session filter applied: {len(events)} -> {len(filtered)}")
        return filtered
    
    def _apply_namespace_filter(
        self,
        events: List[Any],
        namespaces: List[str]
    ) -> List[Any]:
        """
        Namespace フィルターを適用する
        
        Args:
            events: イベントのリスト
            namespaces: Namespace のリスト
            
        Returns:
            List[Any]: フィルタリングされたイベントのリスト
            
        要件: 13.4
        """
        if not namespaces:
            return events
        
        filtered = [
            event for event in events
            if isinstance(event, dict) and event.get('namespace') in namespaces
        ]
        
        logger.info(f"Namespace filter applied: {len(events)} -> {len(filtered)}")
        return filtered

    def _sort_results(
        self,
        results: List[Any],
        sort_by: str = "relevance",
        sort_order: str = "desc"
    ) -> List[Any]:
        """
        検索結果をソートする
        
        Args:
            results: 検索結果のリスト
            sort_by: ソートキー（relevance, timestamp_asc, timestamp_desc, actor_id, session_id）
            sort_order: ソート順（asc, desc）
            
        Returns:
            List[Any]: ソートされた検索結果のリスト
            
        要件: 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7
        """
        if not results:
            return results
        
        reverse = (sort_order == "desc")
        
        if sort_by == "relevance":
            # 関連度でソート（降順）
            sorted_results = sorted(
                results,
                key=lambda r: r.get('relevance_score', 0.0) if isinstance(r, dict) else 0.0,
                reverse=True
            )
        elif sort_by == "timestamp_asc":
            # タイムスタンプでソート（昇順）
            sorted_results = sorted(
                results,
                key=lambda r: r.get('timestamp', '') if isinstance(r, dict) else '',
                reverse=False
            )
        elif sort_by == "timestamp_desc":
            # タイムスタンプでソート（降順）
            sorted_results = sorted(
                results,
                key=lambda r: r.get('timestamp', '') if isinstance(r, dict) else '',
                reverse=True
            )
        elif sort_by == "actor_id":
            # Actor ID でソート
            sorted_results = sorted(
                results,
                key=lambda r: r.get('actor_id', '') if isinstance(r, dict) else '',
                reverse=reverse
            )
        elif sort_by == "session_id":
            # Session ID でソート
            sorted_results = sorted(
                results,
                key=lambda r: r.get('session_id', '') if isinstance(r, dict) else '',
                reverse=reverse
            )
        else:
            sorted_results = results
        
        logger.info(f"Results sorted by: {sort_by} ({sort_order})")
        return sorted_results
    
    def _paginate_results(
        self,
        results: List[Any],
        page: int = 1,
        page_size: int = 20
    ) -> Dict[str, Any]:
        """
        検索結果をページネーションする
        
        Args:
            results: 検索結果のリスト
            page: ページ番号（1から開始）
            page_size: ページサイズ
            
        Returns:
            Dict[str, Any]: ページネーション情報を含む結果
            
        要件: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7
        """
        # ページサイズを制限
        if page_size > MAX_PAGE_SIZE:
            page_size = MAX_PAGE_SIZE
            logger.warning(f"Page size limited to maximum: {MAX_PAGE_SIZE}")
        
        total_count = len(results)
        total_pages = (total_count + page_size - 1) // page_size if page_size > 0 else 0
        
        # ページ番号が範囲外の場合は空の結果を返す
        if page < 1 or (total_pages > 0 and page > total_pages):
            return {
                'results': [],
                'page': page,
                'page_size': page_size,
                'total_count': total_count,
                'total_pages': total_pages,
                'has_next': False,
                'has_prev': False,
                'next_page_token': None
            }
        
        start_index = (page - 1) * page_size
        end_index = start_index + page_size
        
        paginated_results = results[start_index:end_index]
        
        # 次のページトークンを生成
        next_page_token = None
        if page < total_pages:
            next_page_token = f"page_{page + 1}"
        
        return {
            'results': paginated_results,
            'page': page,
            'page_size': page_size,
            'total_count': total_count,
            'total_pages': total_pages,
            'has_next': page < total_pages,
            'has_prev': page > 1,
            'next_page_token': next_page_token
        }
    
    def _highlight_matches(
        self,
        text: str,
        query: str,
        marker: str = "<em>"
    ) -> Dict[str, Any]:
        """
        マッチ部分をハイライトする
        
        Args:
            text: 元のテキスト
            query: 検索クエリ
            marker: ハイライトマーカー（開始タグ）
            
        Returns:
            Dict[str, Any]: ハイライト情報
            
        要件: 16.1, 16.2, 16.3, 16.4, 16.6
        """
        if not text or not query:
            return {
                'highlighted_text': text,
                'match_count': 0,
                'match_positions': []
            }
        
        # マーカーの終了タグを決定
        if marker == "<em>":
            end_marker = "</em>"
        else:
            end_marker = marker
        
        # クエリの単語を分割
        query_words = query.split()
        
        # マッチ位置を記録
        match_positions = []
        
        # 各単語についてマッチを検索
        for word in query_words:
            # 大文字小文字を区別しない検索
            pattern = re.compile(re.escape(word), re.IGNORECASE)
            
            for match in pattern.finditer(text):
                match_positions.append({
                    'text': match.group(0),
                    'position': match.start(),
                    'length': len(match.group(0)),
                    'end': match.end()
                })
        
        # マッチ位置を逆順でソート（後ろから置換するため）
        match_positions.sort(key=lambda m: m['position'], reverse=True)
        
        # テキストをハイライト
        highlighted_text = text
        for match in match_positions:
            start = match['position']
            end = match['end']
            highlighted_text = (
                highlighted_text[:start] +
                marker +
                highlighted_text[start:end] +
                end_marker +
                highlighted_text[end:]
            )
        
        return {
            'highlighted_text': highlighted_text,
            'match_count': len(match_positions),
            'match_positions': match_positions
        }
    
    def _extract_context(
        self,
        text: str,
        match_positions: List[Dict[str, Any]],
        context_size: int = 50
    ) -> List[str]:
        """
        マッチ前後のコンテキストを抽出する
        
        Args:
            text: 元のテキスト
            match_positions: マッチ位置のリスト
            context_size: コンテキストサイズ（文字数）
            
        Returns:
            List[str]: コンテキストのリスト
            
        要件: 16.5
        """
        contexts = []
        
        for match in match_positions:
            start = max(0, match['position'] - context_size)
            end = min(len(text), match['end'] + context_size)
            
            context = text[start:end]
            
            # 前後に省略記号を追加
            if start > 0:
                context = "..." + context
            if end < len(text):
                context = context + "..."
            
            contexts.append(context)
        
        return contexts

    def _search_with_filters(
        self,
        tool_parameters: Dict[str, Any]
    ) -> Generator[ToolInvokeMessage]:
        """
        フィルター付き検索を実行する
        
        Args:
            tool_parameters: ツールパラメータ
            
        Yields:
            ToolInvokeMessage: 検索結果
            
        要件: 13.1-13.7, 14.1-14.7, 15.1-15.7, 16.1-16.6
        """
        try:
            # パラメータを取得
            memory_id = self._clean_id_parameter(tool_parameters.get('memory_id', ''))
            query = tool_parameters.get('query', '')
            namespace = tool_parameters.get('namespace', '/')
            
            if not memory_id:
                raise ValueError("Memory ID が必要です")
            
            if not query:
                raise ValueError("検索クエリが必要です")
            
            if not self.memory_client:
                raise ValueError("Memory client が初期化されていません")
            
            yield self.create_text_message(f"🔍 高度な検索を実行中: '{query}' (namespace: '{namespace}')")
            
            # Memory Search API を呼び出し
            result = self.memory_client.retrieve_memories(
                memory_id=memory_id,
                query=query,
                namespace=namespace,
                top_k=1000  # 最大件数を取得してからフィルタリング
            )
            
            # レスポンスからメモリー配列を取得
            memories_list = result.get('memories', []) if isinstance(result, dict) else result
            
            # イテラブルでなければリスト化
            if not isinstance(memories_list, list):
                memories_list = list(memories_list) if hasattr(memories_list, '__iter__') else []
            
            # フィルターを適用
            filters = tool_parameters.get('filters', {})
            if isinstance(filters, str):
                try:
                    filters = json.loads(filters)
                except json.JSONDecodeError:
                    filters = {}
            
            # 時間範囲フィルター
            start_time = filters.get('start_time')
            end_time = filters.get('end_time')
            if start_time or end_time:
                memories_list = self._apply_time_range_filter(memories_list, start_time, end_time)
            
            # Actor ID フィルター
            actor_ids = filters.get('actor_ids', [])
            if actor_ids:
                memories_list = self._apply_actor_filter(memories_list, actor_ids)
            
            # Session ID フィルター
            session_ids = filters.get('session_ids', [])
            if session_ids:
                memories_list = self._apply_session_filter(memories_list, session_ids)
            
            # Namespace フィルター
            namespaces = filters.get('namespaces', [])
            if namespaces:
                memories_list = self._apply_namespace_filter(memories_list, namespaces)
            
            # ソート
            sort_by = tool_parameters.get('sort_by', 'relevance')
            sort_order = tool_parameters.get('sort_order', 'desc')
            memories_list = self._sort_results(memories_list, sort_by, sort_order)
            
            # ハイライト
            enable_highlight = tool_parameters.get('enable_highlight', False)
            highlight_marker = tool_parameters.get('highlight_marker', '<em>')
            context_size = tool_parameters.get('context_size', 50)
            
            if enable_highlight:
                for memory in memories_list:
                    if isinstance(memory, dict) and 'content' in memory:
                        highlight_info = self._highlight_matches(
                            memory['content'],
                            query,
                            highlight_marker
                        )
                        
                        memory['highlight'] = {
                            'content': highlight_info['highlighted_text'],
                            'matches': {
                                'count': highlight_info['match_count'],
                                'positions': highlight_info['match_positions']
                            }
                        }
                        
                        # コンテキストを抽出
                        if highlight_info['match_positions']:
                            contexts = self._extract_context(
                                memory['content'],
                                highlight_info['match_positions'],
                                context_size
                            )
                            memory['highlight']['context'] = contexts
            
            # ページネーション
            page = tool_parameters.get('page', 1)
            page_size = tool_parameters.get('page_size', 20)
            
            paginated_result = self._paginate_results(memories_list, page, page_size)
            
            # JSON シリアライズしやすい形へ変換
            processed_memories = []
            for memory in paginated_result['results']:
                if isinstance(memory, dict):
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
                'message': f"{paginated_result['total_count']} 件の結果が見つかりました",
                'data': {
                    'memory_id': memory_id,
                    'query': query,
                    'namespace': namespace,
                    'filters_applied': filters,
                    'sort_by': sort_by,
                    'sort_order': sort_order,
                    'highlight_enabled': enable_highlight,
                    'page': paginated_result['page'],
                    'page_size': paginated_result['page_size'],
                    'total_count': paginated_result['total_count'],
                    'total_pages': paginated_result['total_pages'],
                    'has_next': paginated_result['has_next'],
                    'has_prev': paginated_result['has_prev'],
                    'next_page_token': paginated_result['next_page_token'],
                    'results': processed_memories
                }
            }
            
            yield self.create_json_message(response_data)
            
        except ValueError as e:
            logger.error(f"Search error: {str(e)}")
            yield self.create_text_message(f"❌ 検索エラー: {str(e)}")
        except Exception as e:
            logger.error(f"Search error: {str(e)}")
            yield self.create_text_message(f"❌ 検索エラー: {str(e)}")
    
    def _invoke(self, tool_parameters: Dict[str, Any]) -> Generator[ToolInvokeMessage]:
        """
        メインエントリポイント
        
        Args:
            tool_parameters: ツールパラメータ
            
        Yields:
            ToolInvokeMessage: 実行結果
        """
        try:
            # クライアントを初期化
            if not self._initialize_memory_client(tool_parameters):
                yield self.create_text_message("❌ Memory client の初期化に失敗しました")
                return
            
            # 高度な検索を実行
            yield from self._search_with_filters(tool_parameters)
        
        except Exception as e:
            logger.error(f"Invoke error: {str(e)}", exc_info=True)
            yield self.create_text_message(f"❌ 内部エラー: {str(e)}")
