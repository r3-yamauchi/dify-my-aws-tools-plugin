"""
場所: tools/agentcore/agentcore_memory_query.py
内容: AgentCore Memory の複雑な検索クエリを構築・実行するツール
目的: 複数の検索条件を組み合わせた複雑なクエリを構築し、Memory検索を効率化する

要件: 1.1, 1.2, 1.3, 1.4, 1.5, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8
"""

import json
import logging
import re
import signal
from collections.abc import Generator
from typing import Any, Dict, List, Optional, Tuple
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from utils.utils import resolve_aws_credentials
    from utils.query_storage import QueryStorage
    from utils.storage import LocalStorage
except ModuleNotFoundError:  # pragma: no cover
    from my_aws_tools.utils.utils import resolve_aws_credentials
    from my_aws_tools.utils.query_storage import QueryStorage
    from my_aws_tools.utils.storage import LocalStorage

try:
    from bedrock_agentcore.memory import MemoryClient
    AGENTCORE_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    MemoryClient = None
    AGENTCORE_SDK_AVAILABLE = False

logger = logging.getLogger(__name__)

# クエリの複雑度制限
MAX_QUERY_CONDITIONS = 20  # 最大条件数
MAX_QUERY_NESTING_DEPTH = 5  # 最大ネスト深さ


class AgentCoreMemoryQueryTool(Tool):
    """Memory の複雑な検索クエリを構築・実行するツール"""
    
    memory_client: Any = None
    query_storage: QueryStorage = None
    
    def _clean_id_parameter(self, value: str) -> str:
        """ID 文字列の前後にある引用符を取り除く"""
        if value and isinstance(value, str):
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
        return value
    
    def _initialize_clients(self, tool_parameters: Dict[str, Any]) -> bool:
        """
        MemoryClient とストレージを初期化する
        
        標準的な認証情報取得パターンを使用
        
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
            
            # QueryStorage を初期化（ローカルストレージを使用）
            if not self.query_storage:
                storage = LocalStorage()
                self.query_storage = QueryStorage(storage)
                logger.info("Query storage initialized")
            
            return True
                
        except Exception as e:
            logger.error(f"Failed to initialize clients: {str(e)}")
            return False
    
    def _validate_query_complexity(self, query: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """
        クエリの複雑度を検証する
        
        Args:
            query: クエリオブジェクト
            
        Returns:
            Tuple[bool, Optional[str]]: (有効かどうか, エラーメッセージ)
            
        要件: 20.1
        """
        # 条件数をカウント
        def count_conditions(q: Dict[str, Any], depth: int = 0) -> Tuple[int, int]:
            """条件数と最大ネスト深さを再帰的にカウント"""
            if depth > MAX_QUERY_NESTING_DEPTH:
                return -1, depth
            
            conditions = q.get('conditions', [])
            total_count = len(conditions)
            max_depth = depth
            
            # ネストされたクエリを探す
            for condition in conditions:
                if isinstance(condition, dict):
                    # ネストされたクエリの場合
                    if 'conditions' in condition and 'operator' in condition:
                        nested_count, nested_depth = count_conditions(condition, depth + 1)
                        if nested_count == -1:  # ネスト深さ超過
                            return -1, nested_depth
                        total_count += nested_count
                        max_depth = max(max_depth, nested_depth)
            
            return total_count, max_depth
        
        condition_count, nesting_depth = count_conditions(query)
        
        # ネスト深さチェック
        if condition_count == -1:
            error_msg = f"クエリのネスト深さが制限を超えています（最大: {MAX_QUERY_NESTING_DEPTH}）"
            logger.error(error_msg)
            return False, error_msg
        
        # 条件数チェック
        if condition_count > MAX_QUERY_CONDITIONS:
            error_msg = f"クエリの条件数が制限を超えています: {condition_count} 件（最大: {MAX_QUERY_CONDITIONS} 件）"
            logger.error(error_msg)
            return False, error_msg
        
        logger.info(f"Query complexity validated: {condition_count} conditions, depth {nesting_depth}")
        return True, None
    
    def _build_query(self, tool_parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        クエリオブジェクトを構築する
        
        Args:
            tool_parameters: ツールパラメータ
            
        Returns:
            Dict[str, Any]: 構築されたクエリオブジェクト
            
        Raises:
            ValueError: クエリの複雑度が制限を超える場合
            
        要件: 1.1, 20.1
        """
        # 空のクエリオブジェクトを初期化
        query = {
            "conditions": [],
            "operator": "AND"
        }
        
        # パラメータから条件を構築
        conditions_param = tool_parameters.get('conditions')
        if conditions_param:
            # JSON文字列の場合はパース
            if isinstance(conditions_param, str):
                try:
                    conditions_param = json.loads(conditions_param)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse conditions JSON: {conditions_param}")
                    conditions_param = []
            
            if isinstance(conditions_param, list):
                query["conditions"] = conditions_param
        
        # 演算子を設定
        operator = tool_parameters.get('operator', 'AND')
        if operator in ['AND', 'OR', 'NOT']:
            query["operator"] = operator
        
        # クエリの複雑度を検証
        is_valid, error_msg = self._validate_query_complexity(query)
        if not is_valid:
            raise ValueError(error_msg)
        
        logger.info(f"Query built with {len(query['conditions'])} conditions and operator: {query['operator']}")
        return query
    
    def _add_text_condition(
        self,
        query: Dict[str, Any],
        field: str,
        value: str,
        operator: str = "contains"
    ) -> Dict[str, Any]:
        """
        テキスト検索条件を追加する
        
        Args:
            query: クエリオブジェクト
            field: 検索対象フィールド
            value: 検索値
            operator: 演算子（contains, equals, starts_with, ends_with）
            
        Returns:
            Dict[str, Any]: 更新されたクエリオブジェクト
            
        要件: 1.2
        """
        condition = {
            "type": "text",
            "field": field,
            "value": value,
            "operator": operator
        }
        
        query["conditions"].append(condition)
        logger.info(f"Text condition added: {field} {operator} '{value}'")
        return query
    
    def _validate_regex_pattern(self, pattern: str) -> Tuple[bool, Optional[str]]:
        """
        正規表現パターンを検証する
        
        Args:
            pattern: 正規表現パターン
            
        Returns:
            Tuple[bool, Optional[str]]: (有効かどうか, エラーメッセージ)
            
        要件: 2.4
        """
        try:
            re.compile(pattern)
            return True, None
        except re.error as e:
            error_msg = f"無効な正規表現パターンです: {str(e)}"
            logger.error(error_msg)
            return False, error_msg
    
    def _add_regex_condition(
        self,
        query: Dict[str, Any],
        pattern: str,
        field: str = "content",
        case_insensitive: bool = False,
        multiline: bool = False,
        timeout: int = 5
    ) -> Dict[str, Any]:
        """
        正規表現検索条件を追加する
        
        Args:
            query: クエリオブジェクト
            pattern: 正規表現パターン
            field: 検索対象フィールド
            case_insensitive: 大文字小文字を区別しないオプション
            multiline: マルチラインオプション
            timeout: タイムアウト（秒）
            
        Returns:
            Dict[str, Any]: 更新されたクエリオブジェクト
            
        Raises:
            ValueError: 正規表現パターンが無効な場合
            
        要件: 2.1, 2.2, 2.3, 2.4, 2.5
        """
        # 正規表現パターンを検証
        is_valid, error_msg = self._validate_regex_pattern(pattern)
        if not is_valid:
            raise ValueError(error_msg)
        
        # エスケープが必要な文字を自動的にエスケープ
        # ただし、既にエスケープされている場合は二重エスケープしない
        escaped_pattern = pattern
        
        condition = {
            "type": "regex",
            "field": field,
            "pattern": escaped_pattern,
            "case_insensitive": case_insensitive,
            "multiline": multiline,
            "timeout": timeout
        }
        
        query["conditions"].append(condition)
        logger.info(f"Regex condition added: {field} matches /{escaped_pattern}/ (case_insensitive={case_insensitive}, multiline={multiline})")
        return query
    
    def _apply_regex_search(
        self,
        text: str,
        pattern: str,
        case_insensitive: bool = False,
        multiline: bool = False,
        timeout: int = 5
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """
        テキストに対して正規表現検索を実行する
        
        Args:
            text: 検索対象テキスト
            pattern: 正規表現パターン
            case_insensitive: 大文字小文字を区別しないオプション
            multiline: マルチラインオプション
            timeout: タイムアウト（秒）
            
        Returns:
            Tuple[bool, List[Dict[str, Any]]]: (マッチしたかどうか, マッチ情報のリスト)
            
        要件: 2.1, 2.2, 2.3, 2.5
        """
        # 正規表現フラグを設定
        flags = 0
        if case_insensitive:
            flags |= re.IGNORECASE
        if multiline:
            flags |= re.MULTILINE
        
        try:
            # タイムアウト処理を設定
            def timeout_handler(signum, frame):
                raise TimeoutError("正規表現検索がタイムアウトしました")
            
            # Windowsではsignalが制限されているため、タイムアウトは簡易実装
            try:
                signal.signal(signal.SIGALRM, timeout_handler)
                signal.alarm(timeout)
            except (AttributeError, ValueError):
                # Windows環境などでSIGALRMが使えない場合はスキップ
                pass
            
            # 正規表現検索を実行
            compiled_pattern = re.compile(pattern, flags)
            matches = list(compiled_pattern.finditer(text))
            
            # タイムアウトをキャンセル
            try:
                signal.alarm(0)
            except (AttributeError, ValueError):
                pass
            
            # マッチ情報を構築
            match_info = []
            for match in matches:
                match_info.append({
                    "text": match.group(0),
                    "position": match.start(),
                    "length": len(match.group(0)),
                    "end": match.end()
                })
            
            return len(matches) > 0, match_info
            
        except TimeoutError as e:
            logger.error(f"Regex search timeout: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Regex search error: {str(e)}")
            raise
    
    def _highlight_matches(
        self,
        text: str,
        matches: List[Dict[str, Any]],
        marker: str = "<em>"
    ) -> str:
        """
        マッチした部分をハイライトする
        
        Args:
            text: 元のテキスト
            matches: マッチ情報のリスト
            marker: ハイライトマーカー（開始タグ）
            
        Returns:
            str: ハイライトされたテキスト
            
        要件: 2.6
        """
        if not matches:
            return text
        
        # マーカーの終了タグを決定
        if marker == "<em>":
            end_marker = "</em>"
        else:
            end_marker = marker
        
        # マッチ位置を逆順でソート（後ろから置換するため）
        sorted_matches = sorted(matches, key=lambda m: m["position"], reverse=True)
        
        # テキストをハイライト
        highlighted_text = text
        for match in sorted_matches:
            start = match["position"]
            end = match["end"]
            highlighted_text = (
                highlighted_text[:start] +
                marker +
                highlighted_text[start:end] +
                end_marker +
                highlighted_text[end:]
            )
        
        return highlighted_text
    
    def _add_similarity_condition(
        self,
        query: Dict[str, Any],
        text: str,
        threshold: float = 0.7,
        field: str = "content"
    ) -> Dict[str, Any]:
        """
        類似度検索条件を追加する
        
        Args:
            query: クエリオブジェクト
            text: クエリテキスト（ベクトル化される）
            threshold: 類似度閾値（0.0-1.0）
            field: 検索対象フィールド
            
        Returns:
            Dict[str, Any]: 更新されたクエリオブジェクト
            
        要件: 3.1, 3.2, 3.8
        """
        # 閾値の検証
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"類似度閾値は0.0から1.0の範囲で指定してください: {threshold}")
        
        condition = {
            "type": "similarity",
            "field": field,
            "text": text,
            "threshold": threshold
        }
        
        query["conditions"].append(condition)
        logger.info(f"Similarity condition added: {field} similar to '{text}' (threshold={threshold})")
        return query
    
    def _apply_similarity_filtering(
        self,
        memories: List[Any],
        similarity_conditions: List[Dict[str, Any]],
        operator: str = "AND"
    ) -> List[Any]:
        """
        類似度フィルタリングとソートを適用する
        
        Args:
            memories: メモリーのリスト
            similarity_conditions: 類似度検索条件のリスト
            operator: 条件の結合演算子（AND, OR）
            
        Returns:
            List[Any]: フィルタリングおよびソートされたメモリーのリスト
            
        要件: 3.2, 3.3, 3.4, 3.5, 3.6
        """
        if not similarity_conditions:
            return memories
        
        filtered_memories = []
        
        for memory in memories:
            # メモリーから類似度スコアを取得
            # bedrock-agentcore の retrieve_memories は類似度スコアを返す
            similarity_score = None
            
            if isinstance(memory, dict):
                # スコアフィールドを探す（複数の可能性を考慮）
                # 0や0.0も有効な値として扱うため、is not Noneで判定
                if 'similarity_score' in memory:
                    similarity_score = memory['similarity_score']
                elif 'score' in memory:
                    similarity_score = memory['score']
                elif 'relevance_score' in memory:
                    similarity_score = memory['relevance_score']
                elif 'confidence' in memory:
                    similarity_score = memory['confidence']
            
            # スコアが取得できない場合はデフォルト値を使用
            # ただし、0や0.0は有効な値として扱う
            if similarity_score is None:
                similarity_score = 1.0  # デフォルトで最高スコア
            
            # メモリーのコピーを作成してスコアを記録
            if isinstance(memory, dict):
                memory_copy = memory.copy()
                memory_copy['similarity_score'] = similarity_score
            else:
                memory_copy = memory
            
            # 複数の類似度条件を評価
            if operator == "OR":
                # OR: いずれかの条件を満たせば良い
                meets_condition = False
                
                for condition in similarity_conditions:
                    threshold = condition.get("threshold", 0.7)
                    if similarity_score >= threshold:
                        meets_condition = True
                        break
                
                if meets_condition:
                    filtered_memories.append(memory_copy)
            
            else:  # AND
                # AND: すべての条件を満たす必要がある
                meets_all_conditions = True
                
                for condition in similarity_conditions:
                    threshold = condition.get("threshold", 0.7)
                    if similarity_score < threshold:
                        meets_all_conditions = False
                        break
                
                if meets_all_conditions:
                    filtered_memories.append(memory_copy)
        
        # 類似度でソート（降順）
        filtered_memories.sort(
            key=lambda m: m.get('similarity_score', 0.0) if isinstance(m, dict) else 0.0,
            reverse=True
        )
        
        logger.info(f"Similarity filtering applied: {len(filtered_memories)} memories passed threshold")
        return filtered_memories
    
    def _combine_conditions(
        self,
        conditions: List[Dict[str, Any]],
        operator: str
    ) -> Dict[str, Any]:
        """
        条件を AND/OR/NOT で結合する
        
        Args:
            conditions: 条件のリスト
            operator: 結合演算子（AND, OR, NOT）
            
        Returns:
            Dict[str, Any]: 結合されたクエリオブジェクト
            
        Raises:
            ValueError: クエリの複雑度が制限を超える場合
            
        要件: 1.3, 1.4, 1.5, 20.1
        """
        if operator not in ['AND', 'OR', 'NOT']:
            raise ValueError(f"無効な演算子です: {operator}. AND, OR, NOT のいずれかを指定してください。")
        
        # NOT演算子の場合は条件が1つだけであることを確認
        if operator == 'NOT' and len(conditions) != 1:
            raise ValueError("NOT演算子は1つの条件のみを受け付けます")
        
        query = {
            "conditions": conditions,
            "operator": operator
        }
        
        # クエリの複雑度を検証
        is_valid, error_msg = self._validate_query_complexity(query)
        if not is_valid:
            raise ValueError(error_msg)
        
        logger.info(f"Conditions combined with {operator}: {len(conditions)} conditions")
        return query
    
    def _execute_query(
        self,
        query: Dict[str, Any],
        memory_id: str,
        namespace: str = "/",
        max_results: int = 10,
        enable_highlight: bool = False,
        highlight_marker: str = "<em>"
    ) -> Generator[ToolInvokeMessage]:
        """
        クエリを実行して結果を返す
        
        Args:
            query: クエリオブジェクト
            memory_id: Memory ID
            namespace: Namespace
            max_results: 最大結果数
            enable_highlight: ハイライトを有効にするかどうか
            highlight_marker: ハイライトマーカー
            
        Yields:
            ToolInvokeMessage: 検索結果
            
        要件: 1.7, 1.8, 2.6, 2.7, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6
        """
        try:
            # クエリが空の場合はすべての結果を返す
            if not query.get("conditions"):
                search_query = "all"
                yield self.create_text_message("🔍 クエリが空です。すべての結果を返します。")
            else:
                # クエリから検索文字列を構築
                search_query = self._build_search_string(query)
                yield self.create_text_message(f"🔍 クエリを実行中: '{search_query}' (namespace: '{namespace}')")
            
            if not self.memory_client:
                yield self.create_text_message("❌ Memory client が初期化されていません")
                return
            
            # 類似度検索条件を抽出
            similarity_conditions = [c for c in query.get("conditions", []) if c.get("type") == "similarity"]
            
            # 類似度検索がある場合は、最初の類似度条件のテキストを使用
            if similarity_conditions:
                # 複数の類似度条件がある場合は OR 検索として扱う
                search_query = similarity_conditions[0].get("text", search_query)
                yield self.create_text_message(f"🔍 類似度検索を実行中: '{search_query}'")
            
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
            
            # 類似度フィルタリングとソートを適用
            if similarity_conditions:
                memories_list = self._apply_similarity_filtering(
                    memories_list,
                    similarity_conditions,
                    query.get("operator", "AND")
                )
            
            # 正規表現フィルタリングを適用
            filtered_memories = []
            regex_conditions = [c for c in query.get("conditions", []) if c.get("type") == "regex"]
            
            for memory in memories_list:
                # 正規表現条件がある場合はフィルタリング
                if regex_conditions:
                    matches_all = True
                    memory_matches = {}
                    
                    for condition in regex_conditions:
                        pattern = condition.get("pattern", "")
                        field = condition.get("field", "content")
                        case_insensitive = condition.get("case_insensitive", False)
                        multiline = condition.get("multiline", False)
                        timeout = condition.get("timeout", 5)
                        
                        # メモリーからフィールド値を取得
                        if isinstance(memory, dict):
                            text = memory.get(field, "")
                        else:
                            text = str(memory)
                        
                        # 正規表現検索を実行
                        try:
                            is_match, match_info = self._apply_regex_search(
                                text=text,
                                pattern=pattern,
                                case_insensitive=case_insensitive,
                                multiline=multiline,
                                timeout=timeout
                            )
                            
                            if not is_match:
                                matches_all = False
                                break
                            
                            # マッチ情報を保存
                            memory_matches[field] = match_info
                            
                        except TimeoutError:
                            yield self.create_text_message(f"⚠️ 正規表現検索がタイムアウトしました: パターン '{pattern}'")
                            matches_all = False
                            break
                        except Exception as e:
                            logger.error(f"Regex search error: {str(e)}")
                            matches_all = False
                            break
                    
                    if matches_all:
                        # ハイライトを適用
                        if enable_highlight and memory_matches:
                            if isinstance(memory, dict):
                                memory = memory.copy()
                                for field, match_info in memory_matches.items():
                                    if field in memory:
                                        memory[field] = self._highlight_matches(
                                            memory[field],
                                            match_info,
                                            highlight_marker
                                        )
                                        # マッチ情報を追加
                                        memory[f"{field}_matches"] = {
                                            "count": len(match_info),
                                            "positions": match_info
                                        }
                        
                        filtered_memories.append(memory)
                else:
                    filtered_memories.append(memory)
            
            # 取得数を max_results で制限
            if max_results and len(filtered_memories) > max_results:
                filtered_memories = filtered_memories[:max_results]
            
            # JSON シリアライズしやすい形へ変換
            processed_memories = []
            for memory in filtered_memories:
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
                'message': f"{len(processed_memories)} 件の結果が見つかりました",
                'data': {
                    'memories_count': len(processed_memories),
                    'memory_id': memory_id,
                    'namespace': namespace,
                    'query': query,
                    'search_string': search_query,
                    'highlight_enabled': enable_highlight,
                    'memories': processed_memories
                }
            }
            
            yield self.create_json_message(response_data)
            
        except Exception as e:
            logger.error(f"Query execution error: {str(e)}")
            yield self.create_text_message(f"❌ クエリ実行エラー: {str(e)}")
    
    def _build_search_string(self, query: Dict[str, Any]) -> str:
        """
        クエリオブジェクトから検索文字列を構築する
        
        Args:
            query: クエリオブジェクト
            
        Returns:
            str: 検索文字列
        """
        conditions = query.get("conditions", [])
        operator = query.get("operator", "AND")
        
        if not conditions:
            return "all"
        
        # テキスト条件から検索文字列を抽出
        search_terms = []
        for condition in conditions:
            if condition.get("type") == "text":
                value = condition.get("value", "")
                if value:
                    search_terms.append(value)
        
        if not search_terms:
            return "all"
        
        # 演算子に応じて結合
        if operator == "AND":
            return " AND ".join(search_terms)
        elif operator == "OR":
            return " OR ".join(search_terms)
        elif operator == "NOT":
            return f"NOT {search_terms[0]}"
        else:
            return " ".join(search_terms)
    
    def _save_query(
        self,
        query: Dict[str, Any],
        name: str,
        description: str = ""
    ) -> Generator[ToolInvokeMessage]:
        """
        クエリを保存する
        
        Args:
            query: クエリオブジェクト
            name: クエリ名
            description: クエリの説明
            
        Yields:
            ToolInvokeMessage: 保存結果
            
        要件: 4.1, 4.2
        """
        try:
            if not self.query_storage:
                yield self.create_text_message("❌ Query storage が初期化されていません")
                return
            
            query_id = self.query_storage.save_query(
                query=query,
                name=name,
                description=description
            )
            
            response_data = {
                'success': True,
                'message': f"クエリを保存しました: {name}",
                'data': {
                    'query_id': query_id,
                    'name': name,
                    'description': description
                }
            }
            
            yield self.create_json_message(response_data)
            
        except ValueError as e:
            logger.error(f"Save query error: {str(e)}")
            yield self.create_text_message(f"❌ クエリ保存エラー: {str(e)}")
        except Exception as e:
            logger.error(f"Save query error: {str(e)}")
            yield self.create_text_message(f"❌ クエリ保存エラー: {str(e)}")
    
    def _load_query(self, query_id: str) -> Generator[ToolInvokeMessage]:
        """
        保存されたクエリを読み込む
        
        Args:
            query_id: クエリID
            
        Yields:
            ToolInvokeMessage: 読み込み結果
            
        要件: 4.3
        """
        try:
            if not self.query_storage:
                yield self.create_text_message("❌ Query storage が初期化されていません")
                return
            
            query_data = self.query_storage.load_query(query_id)
            
            if not query_data:
                yield self.create_text_message(f"❌ クエリが見つかりません: {query_id}")
                return
            
            response_data = {
                'success': True,
                'message': f"クエリを読み込みました: {query_data.get('name')}",
                'data': query_data
            }
            
            yield self.create_json_message(response_data)
            
        except Exception as e:
            logger.error(f"Load query error: {str(e)}")
            yield self.create_text_message(f"❌ クエリ読み込みエラー: {str(e)}")
    
    def _list_queries(self, name_filter: Optional[str] = None) -> Generator[ToolInvokeMessage]:
        """
        保存されたクエリの一覧を取得する
        
        Args:
            name_filter: クエリ名のフィルター（部分一致）
            
        Yields:
            ToolInvokeMessage: クエリ一覧
            
        要件: 4.4
        """
        try:
            if not self.query_storage:
                yield self.create_text_message("❌ Query storage が初期化されていません")
                return
            
            queries = self.query_storage.list_queries(name_filter=name_filter)
            
            response_data = {
                'success': True,
                'message': f"{len(queries)} 件のクエリが見つかりました",
                'data': {
                    'count': len(queries),
                    'queries': queries
                }
            }
            
            yield self.create_json_message(response_data)
            
        except Exception as e:
            logger.error(f"List queries error: {str(e)}")
            yield self.create_text_message(f"❌ クエリ一覧取得エラー: {str(e)}")
    
    def _delete_query(self, query_id: str) -> Generator[ToolInvokeMessage]:
        """
        クエリを削除する
        
        Args:
            query_id: クエリID
            
        Yields:
            ToolInvokeMessage: 削除結果
            
        要件: 4.5
        """
        try:
            if not self.query_storage:
                yield self.create_text_message("❌ Query storage が初期化されていません")
                return
            
            result = self.query_storage.delete_query(query_id)
            
            if result:
                response_data = {
                    'success': True,
                    'message': f"クエリを削除しました: {query_id}",
                    'data': {
                        'query_id': query_id
                    }
                }
                yield self.create_json_message(response_data)
            else:
                yield self.create_text_message(f"❌ クエリが見つかりません: {query_id}")
            
        except Exception as e:
            logger.error(f"Delete query error: {str(e)}")
            yield self.create_text_message(f"❌ クエリ削除エラー: {str(e)}")
    
    def _update_query(
        self,
        query_id: str,
        query: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
        description: Optional[str] = None
    ) -> Generator[ToolInvokeMessage]:
        """
        クエリを更新する
        
        Args:
            query_id: クエリID
            query: 新しいクエリオブジェクト（オプション）
            name: 新しいクエリ名（オプション）
            description: 新しい説明（オプション）
            
        Yields:
            ToolInvokeMessage: 更新結果
            
        要件: 4.6
        """
        try:
            if not self.query_storage:
                yield self.create_text_message("❌ Query storage が初期化されていません")
                return
            
            result = self.query_storage.update_query(
                query_id=query_id,
                query=query,
                name=name,
                description=description
            )
            
            if result:
                response_data = {
                    'success': True,
                    'message': f"クエリを更新しました: {query_id}",
                    'data': {
                        'query_id': query_id
                    }
                }
                yield self.create_json_message(response_data)
            else:
                yield self.create_text_message(f"❌ クエリの更新に失敗しました: {query_id}")
            
        except ValueError as e:
            logger.error(f"Update query error: {str(e)}")
            yield self.create_text_message(f"❌ クエリ更新エラー: {str(e)}")
        except Exception as e:
            logger.error(f"Update query error: {str(e)}")
            yield self.create_text_message(f"❌ クエリ更新エラー: {str(e)}")
    
    def _export_query(self, query_id: str) -> Generator[ToolInvokeMessage]:
        """
        クエリをエクスポートする
        
        Args:
            query_id: クエリID
            
        Yields:
            ToolInvokeMessage: エクスポート結果
            
        要件: 4.7
        """
        try:
            if not self.query_storage:
                yield self.create_text_message("❌ Query storage が初期化されていません")
                return
            
            query_data = self.query_storage.export_query(query_id)
            
            response_data = {
                'success': True,
                'message': f"クエリをエクスポートしました: {query_id}",
                'data': query_data
            }
            
            yield self.create_json_message(response_data)
            
        except ValueError as e:
            logger.error(f"Export query error: {str(e)}")
            yield self.create_text_message(f"❌ クエリエクスポートエラー: {str(e)}")
        except Exception as e:
            logger.error(f"Export query error: {str(e)}")
            yield self.create_text_message(f"❌ クエリエクスポートエラー: {str(e)}")
    
    def _import_query(
        self,
        query_data: Dict[str, Any],
        overwrite: bool = False
    ) -> Generator[ToolInvokeMessage]:
        """
        クエリをインポートする
        
        Args:
            query_data: インポートするクエリデータ
            overwrite: 既存のクエリを上書きするかどうか
            
        Yields:
            ToolInvokeMessage: インポート結果
            
        要件: 4.8
        """
        try:
            if not self.query_storage:
                yield self.create_text_message("❌ Query storage が初期化されていません")
                return
            
            query_id = self.query_storage.import_query(
                query_data=query_data,
                overwrite=overwrite
            )
            
            response_data = {
                'success': True,
                'message': f"クエリをインポートしました: {query_id}",
                'data': {
                    'query_id': query_id
                }
            }
            
            yield self.create_json_message(response_data)
            
        except ValueError as e:
            logger.error(f"Import query error: {str(e)}")
            yield self.create_text_message(f"❌ クエリインポートエラー: {str(e)}")
        except Exception as e:
            logger.error(f"Import query error: {str(e)}")
            yield self.create_text_message(f"❌ クエリインポートエラー: {str(e)}")
    
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
            if not self._initialize_clients(tool_parameters):
                yield self.create_text_message("❌ クライアントの初期化に失敗しました")
                return
            
            # 操作タイプを取得
            operation = tool_parameters.get('operation', 'build')
            
            if operation == 'build':
                # クエリを構築
                query = self._build_query(tool_parameters)
                response_data = {
                    'success': True,
                    'message': 'クエリを構築しました',
                    'data': {
                        'query': query
                    }
                }
                yield self.create_json_message(response_data)
            
            elif operation == 'execute':
                # クエリを実行
                query = self._build_query(tool_parameters)
                memory_id = self._clean_id_parameter(tool_parameters.get('memory_id', ''))
                namespace = tool_parameters.get('namespace', '/')
                max_results = tool_parameters.get('max_results', 10)
                enable_highlight = tool_parameters.get('enable_highlight', False)
                highlight_marker = tool_parameters.get('highlight_marker', '<em>')
                
                if not memory_id:
                    yield self.create_text_message("❌ Memory ID が必要です")
                    return
                
                yield from self._execute_query(
                    query,
                    memory_id,
                    namespace,
                    max_results,
                    enable_highlight,
                    highlight_marker
                )
            
            elif operation == 'save':
                # クエリを保存
                query = self._build_query(tool_parameters)
                name = tool_parameters.get('query_name', '')
                description = tool_parameters.get('query_description', '')
                
                if not name:
                    yield self.create_text_message("❌ クエリ名が必要です")
                    return
                
                yield from self._save_query(query, name, description)
            
            elif operation == 'load':
                # クエリを読み込み
                query_id = self._clean_id_parameter(tool_parameters.get('query_id', ''))
                
                if not query_id:
                    yield self.create_text_message("❌ クエリIDが必要です")
                    return
                
                yield from self._load_query(query_id)
            
            elif operation == 'list':
                # クエリ一覧を取得
                name_filter = tool_parameters.get('name_filter')
                yield from self._list_queries(name_filter)
            
            elif operation == 'delete':
                # クエリを削除
                query_id = self._clean_id_parameter(tool_parameters.get('query_id', ''))
                
                if not query_id:
                    yield self.create_text_message("❌ クエリIDが必要です")
                    return
                
                yield from self._delete_query(query_id)
            
            elif operation == 'update':
                # クエリを更新
                query_id = self._clean_id_parameter(tool_parameters.get('query_id', ''))
                
                if not query_id:
                    yield self.create_text_message("❌ クエリIDが必要です")
                    return
                
                # 更新するフィールドを取得
                query = None
                if tool_parameters.get('conditions'):
                    query = self._build_query(tool_parameters)
                
                name = tool_parameters.get('query_name')
                description = tool_parameters.get('query_description')
                
                yield from self._update_query(query_id, query, name, description)
            
            elif operation == 'export':
                # クエリをエクスポート
                query_id = self._clean_id_parameter(tool_parameters.get('query_id', ''))
                
                if not query_id:
                    yield self.create_text_message("❌ クエリIDが必要です")
                    return
                
                yield from self._export_query(query_id)
            
            elif operation == 'import':
                # クエリをインポート
                query_data_str = tool_parameters.get('query_data', '')
                
                if not query_data_str:
                    yield self.create_text_message("❌ クエリデータが必要です")
                    return
                
                # JSON文字列をパース
                try:
                    query_data = json.loads(query_data_str) if isinstance(query_data_str, str) else query_data_str
                except json.JSONDecodeError as e:
                    yield self.create_text_message(f"❌ クエリデータのJSON解析に失敗しました: {str(e)}")
                    return
                
                overwrite = tool_parameters.get('overwrite', False)
                yield from self._import_query(query_data, overwrite)
            
            else:
                yield self.create_text_message(f"❌ 無効な操作です: {operation}")
        
        except Exception as e:
            logger.error(f"Invoke error: {str(e)}", exc_info=True)
            yield self.create_text_message(f"❌ 内部エラー: {str(e)}")
