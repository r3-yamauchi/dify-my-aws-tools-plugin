"""
場所: tools/agentcore/agentcore_memory_statistics.py
内容: Bedrock AgentCore Memory リソースの統計情報を取得・分析する Dify ツール。
目的: Memory リソースの使用状況を可視化し、コスト分析やユーザーアクティビティの把握を支援する。
"""

import json
import logging
import os
import sys
import time
from collections.abc import Generator
from typing import Any, Dict, Optional, List, Tuple, Callable
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

# 相対インポートとフルパスインポートの両方に対応
try:
    from utils.utils import resolve_aws_credentials
    from utils.error_handler import AgentCoreError
    from utils.time_utils import TimeUtils
except ModuleNotFoundError:  # pragma: no cover
    from my_aws_tools.utils.utils import resolve_aws_credentials
    from my_aws_tools.utils.error_handler import AgentCoreError
    from my_aws_tools.utils.time_utils import TimeUtils

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


class AgentCoreMemoryStatisticsTool(Tool):
    """Memory リソースの統計情報を取得・分析するツール本体."""

    memory_client: Any = None
    _statistics_cache: Dict[str, Tuple[float, Any]] = {}  # キャッシュ: {key: (timestamp, data)}
    _cache_ttl: int = 300  # キャッシュ有効期限（秒）

    # ------------------------------------------------------------------
    # 初期化
    # ------------------------------------------------------------------
    def _initialize_memory_client(self, tool_parameters: dict[str, Any]) -> tuple[bool, Optional[str]]:
        """
        AWS 資格情報から MemoryClient を構築する。
        
        Args:
            tool_parameters: ツールパラメータ（認証情報を含む）
            
        Returns:
            tuple[bool, Optional[str]]: (初期化が成功した場合 True、エラーメッセージまたは None)
            
        要件: 9.1, 9.2, 9.3, 9.4, 9.5
        """
        if not AGENTCORE_SDK_AVAILABLE:
            error_msg = "❌ AgentCore Memory SDK が利用できません。bedrock-agentcore パッケージをインストールしてください。"
            logger.error("AgentCore Memory SDK not available")
            return False, error_msg

        try:
            # 既存の認証情報解決機構を使用（要件 9.1, 9.2, 9.3）
            # 優先順位: ツールパラメータ > プロバイダー設定 > 環境変数
            credentials = resolve_aws_credentials(self, tool_parameters)
            
            # デフォルトリージョンの設定（要件 9.5）
            aws_region = credentials.get("aws_region") or "us-east-1"
            aws_access_key_id = credentials.get("aws_access_key_id")
            aws_secret_access_key = credentials.get("aws_secret_access_key")

            # 明示的な AK/SK が渡された場合は環境変数経由で設定
            # ツールパラメータの認証情報を優先（要件 9.1）
            if aws_access_key_id and aws_secret_access_key:
                os.environ["AWS_ACCESS_KEY_ID"] = aws_access_key_id
                os.environ["AWS_SECRET_ACCESS_KEY"] = aws_secret_access_key
                os.environ["AWS_REGION"] = aws_region

            # MemoryClient を初期化
            self.memory_client = MemoryClient(region_name=aws_region)
            logger.info(f"AgentCore Memory client initialized successfully (region: {aws_region})")
            return True, None
            
        except Exception as exc:
            # 認証エラーのハンドリング（要件 9.4, 10.1）
            logger.error(f"Failed to initialize Memory client: {exc}", exc_info=True)
            
            # エラーの種類に応じた日本語メッセージを生成
            # credentials が未定義の場合は空の辞書を渡す
            error_msg = self._format_initialization_error(exc, locals().get('credentials', {}))
            return False, error_msg
    
    def _format_initialization_error(self, exc: Exception, credentials: Dict[str, Optional[str]]) -> str:
        """
        初期化エラーを日本語のエラーメッセージに変換する。
        
        Args:
            exc: 発生した例外
            credentials: 使用した認証情報
            
        Returns:
            str: 日本語のエラーメッセージ
            
        要件: 9.4, 10.1, 10.2
        """
        from botocore.exceptions import ClientError, NoCredentialsError, PartialCredentialsError
        
        # 認証情報関連のエラー
        if isinstance(exc, NoCredentialsError):
            return (
                "❌ AWS 認証情報が見つかりません。\n"
                "以下のいずれかの方法で認証情報を設定してください：\n"
                "1. ツールパラメータに aws_access_key_id と aws_secret_access_key を指定\n"
                "2. プロバイダー設定で認証情報を設定\n"
                "3. 環境変数 AWS_ACCESS_KEY_ID と AWS_SECRET_ACCESS_KEY を設定"
            )
        
        if isinstance(exc, PartialCredentialsError):
            return (
                "❌ AWS 認証情報が不完全です。\n"
                "aws_access_key_id と aws_secret_access_key の両方を指定してください。"
            )
        
        # ClientError の場合
        if isinstance(exc, ClientError):
            error_code = exc.response.get('Error', {}).get('Code', '')
            error_message = exc.response.get('Error', {}).get('Message', '')
            
            if error_code in ['InvalidClientTokenId', 'SignatureDoesNotMatch', 'AuthFailure']:
                return (
                    f"❌ AWS 認証に失敗しました。\n"
                    f"エラーコード: {error_code}\n"
                    f"認証情報が正しいか確認してください。"
                )
            
            if error_code == 'AccessDeniedException':
                return (
                    f"❌ AWS リソースへのアクセスが拒否されました。\n"
                    f"IAM ポリシーで必要な権限が付与されているか確認してください。\n"
                    f"必要な権限: bedrock:*, bedrock-agentcore:*"
                )
            
            # その他の ClientError
            return (
                f"❌ AWS API エラーが発生しました。\n"
                f"エラーコード: {error_code}\n"
                f"メッセージ: {error_message}"
            )
        
        # その他の例外
        region = credentials.get("aws_region", "us-east-1")
        return (
            f"❌ Memory クライアントの初期化中に予期しないエラーが発生しました。\n"
            f"リージョン: {region}\n"
            f"エラー: {str(exc)}"
        )

    # ------------------------------------------------------------------
    # リトライロジック
    # ------------------------------------------------------------------
    def _execute_with_retry(
        self,
        operation: Callable,
        max_retries: int = 3,
        operation_name: str = "操作"
    ) -> Any:
        """
        リトライロジック付きで操作を実行する。
        
        Args:
            operation: 実行する操作（callable）
            max_retries: 最大リトライ回数（デフォルト: 3）
            operation_name: 操作名（ログ用）
            
        Returns:
            Any: 操作の実行結果
            
        Raises:
            Exception: 最大リトライ回数を超えた場合
            
        要件: 10.4
        """
        from botocore.exceptions import ClientError
        
        for attempt in range(max_retries):
            try:
                return operation()
            except ClientError as e:
                error_code = e.response.get('Error', {}).get('Code', '')
                
                # リトライ可能なエラーかチェック
                if error_code in ['ThrottlingException', 'ServiceUnavailableException']:
                    if attempt < max_retries - 1:
                        # 指数バックオフ（ThrottlingException）または線形バックオフ（ServiceUnavailableException）
                        if error_code == 'ThrottlingException':
                            wait_time = (2 ** attempt)  # 1秒、2秒、4秒
                            logger.info(f"{operation_name}: ThrottlingException detected, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                        else:
                            wait_time = (attempt + 1)  # 1秒、2秒、3秒
                            logger.info(f"{operation_name}: ServiceUnavailableException detected, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                        
                        time.sleep(wait_time)
                        continue
                    else:
                        # 最大リトライ回数に達した
                        logger.error(f"{operation_name}: Max retries reached for {error_code}")
                        raise
                
                elif error_code == 'RequestTimeout':
                    if attempt < max_retries - 1:
                        # RequestTimeout は1回のみリトライ
                        wait_time = 1
                        logger.info(f"{operation_name}: RequestTimeout detected, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.error(f"{operation_name}: Max retries reached for RequestTimeout")
                        raise
                
                else:
                    # リトライ不可能なエラーはすぐに再スロー
                    raise
            
            except Exception as e:
                # ClientError 以外の例外はリトライせずに再スロー
                logger.error(f"{operation_name}: Non-retryable error: {e}")
                raise
        
        # ここには到達しないはずだが、念のため
        raise Exception(f"{operation_name}: Unexpected retry loop exit")
    
    def _execute_with_timeout(
        self,
        operation: Callable,
        timeout_seconds: int,
        operation_name: str = "操作"
    ) -> Any:
        """
        タイムアウト付きで操作を実行する。
        
        Args:
            operation: 実行する操作（callable）
            timeout_seconds: タイムアウト時間（秒）
            operation_name: 操作名（エラーメッセージ用）
            
        Returns:
            Any: 操作の実行結果
            
        Raises:
            TimeoutError: タイムアウトした場合
            
        要件: 3.5
        """
        import signal
        
        def timeout_handler(signum, frame):
            raise TimeoutError(f"{operation_name}が{timeout_seconds}秒でタイムアウトしました")
        
        # シグナルハンドラーを設定（Unix系システムのみ）
        # Windows では signal.SIGALRM が利用できないため、代替手段を使用
        try:
            # Unix系システムの場合
            old_handler = signal.signal(signal.SIGALRM, timeout_handler)
            signal.alarm(timeout_seconds)
            
            try:
                result = operation()
                signal.alarm(0)  # タイムアウトをキャンセル
                signal.signal(signal.SIGALRM, old_handler)  # 元のハンドラーを復元
                return result
            except TimeoutError:
                signal.alarm(0)  # タイムアウトをキャンセル
                signal.signal(signal.SIGALRM, old_handler)  # 元のハンドラーを復元
                raise
            except Exception as e:
                signal.alarm(0)  # タイムアウトをキャンセル
                signal.signal(signal.SIGALRM, old_handler)  # 元のハンドラーを復元
                raise
        
        except (AttributeError, ValueError):
            # Windows または signal.SIGALRM が利用できない環境の場合
            # threading.Timer を使用した代替実装
            import threading
            
            result_container = {"result": None, "exception": None, "completed": False}
            
            def run_operation():
                try:
                    result_container["result"] = operation()
                    result_container["completed"] = True
                except Exception as e:
                    result_container["exception"] = e
                    result_container["completed"] = True
            
            thread = threading.Thread(target=run_operation)
            thread.daemon = True
            thread.start()
            thread.join(timeout=timeout_seconds)
            
            if not result_container["completed"]:
                raise TimeoutError(f"{operation_name}が{timeout_seconds}秒でタイムアウトしました")
            
            if result_container["exception"]:
                raise result_container["exception"]
            
            return result_container["result"]

    # ------------------------------------------------------------------
    # キャッシュ管理
    # ------------------------------------------------------------------
    def _get_from_cache(self, cache_key: str) -> Optional[Any]:
        """
        キャッシュからデータを取得する。
        
        Args:
            cache_key: キャッシュキー
            
        Returns:
            Optional[Any]: キャッシュされたデータ、または None（期限切れまたは存在しない場合）
            
        要件: 3.2, 3.3
        """
        if cache_key in self._statistics_cache:
            timestamp, data = self._statistics_cache[cache_key]
            # キャッシュの有効期限をチェック（要件 3.3）
            if time.time() - timestamp < self._cache_ttl:
                logger.info(f"Cache hit: {cache_key}")
                return data
            else:
                # 期限切れキャッシュを削除
                del self._statistics_cache[cache_key]
                logger.info(f"Cache expired: {cache_key}")
        return None

    def _set_to_cache(self, cache_key: str, data: Any) -> None:
        """
        キャッシュにデータを保存する。
        
        Args:
            cache_key: キャッシュキー
            data: 保存するデータ
            
        要件: 3.2
        """
        self._statistics_cache[cache_key] = (time.time(), data)
        logger.info(f"Cache set: {cache_key}")

    # ------------------------------------------------------------------
    # イベントデータの集計
    # ------------------------------------------------------------------
    def _aggregate_events(self, events: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        イベントデータを集計する。
        
        Args:
            events: イベントのリスト
            
        Returns:
            Dict[str, Any]: 集計結果
            
        要件: 2.1, 2.2, 2.3
        """
        # イベント ID の一意性を確保（要件 2.1）
        unique_event_ids = set()
        actor_ids = set()
        session_ids = set()
        first_timestamp = None
        last_timestamp = None
        
        for event in events:
            event_id = event.get("eventId")
            if event_id:
                unique_event_ids.add(event_id)
            
            # Actor ID を収集（要件 2.2）
            actor_id = event.get("actorId")
            if actor_id:
                actor_ids.add(actor_id)
            
            # Session ID を収集（要件 2.3）
            session_id = event.get("sessionId")
            if session_id:
                session_ids.add(session_id)
            
            # タイムスタンプを追跡
            timestamp = event.get("timestamp")
            if timestamp:
                if first_timestamp is None or timestamp < first_timestamp:
                    first_timestamp = timestamp
                if last_timestamp is None or timestamp > last_timestamp:
                    last_timestamp = timestamp
        
        return {
            "event_count": len(unique_event_ids),
            "actor_count": len(actor_ids),
            "session_count": len(session_ids),
            "first_event_timestamp": first_timestamp,
            "last_event_timestamp": last_timestamp,
        }

    def _calculate_storage_size(self, events: List[Dict[str, Any]]) -> int:
        """
        ストレージサイズを計算する。
        
        Args:
            events: イベントのリスト
            
        Returns:
            int: ストレージサイズ（バイト）
            
        要件: 2.4
        """
        total_size = 0
        for event in events:
            # イベントを JSON 文字列に変換してサイズを計算
            event_json = json.dumps(event, ensure_ascii=False)
            total_size += len(event_json.encode('utf-8'))
        return total_size

    # ------------------------------------------------------------------
    # 基本統計情報取得
    # ------------------------------------------------------------------
    def _get_statistics(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        Memory リソースの基本統計情報を取得する。
        
        Args:
            tool_parameters: ツールパラメータ（memory_id, metrics, use_cache を含む）
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
            
        要件: 1.1, 2.1, 2.2, 2.3, 2.4, 12.1
        """
        memory_id = tool_parameters.get("memory_id", "").strip()
        if not memory_id:
            yield self.create_text_message("❌ Memory ID は必須です")
            return
        
        # メトリクスの取得（カンマ区切り文字列またはリスト）
        metrics_param = tool_parameters.get("metrics")
        if metrics_param:
            if isinstance(metrics_param, str):
                metrics = [m.strip() for m in metrics_param.split(",") if m.strip()]
            elif isinstance(metrics_param, list):
                metrics = [str(m).strip() for m in metrics_param if str(m).strip()]
            else:
                metrics = None
        else:
            metrics = None
        
        # キャッシュの使用設定
        use_cache = tool_parameters.get("use_cache", True)
        
        # キャッシュキーの生成
        cache_key = f"statistics:{memory_id}:{','.join(sorted(metrics)) if metrics else 'all'}"
        
        # キャッシュからデータを取得（要件 3.2）
        if use_cache:
            cached_data = self._get_from_cache(cache_key)
            if cached_data:
                # キャッシュフラグを更新
                cached_data["data"]["cached"] = True
                yield self.create_text_message("📊 キャッシュから統計情報を取得しました")
                yield self.create_json_message(cached_data)
                return
        
        # エラーコンテキストの作成
        context = AgentCoreError.create_error_context(
            operation="get_statistics",
            memory_id=memory_id
        )
        
        yield self.create_text_message(f"📊 Memory 統計情報を取得中: {memory_id}")
        
        try:
            # イベント一覧を取得（ページネーション対応）
            # タイムアウト付きで実行（要件 3.5）
            def fetch_all_events():
                all_events = []
                next_token = None
                max_results_per_page = 100
                
                while True:
                    request_params = {
                        "memory_id": memory_id,
                        "max_results": max_results_per_page
                    }
                    
                    if next_token:
                        request_params["next_token"] = next_token
                    
                    # list_events を呼び出し（リトライロジック付き、要件 2.1, 10.4）
                    response = self._execute_with_retry(
                        lambda: self.memory_client.list_events(**request_params),
                        operation_name="list_events"
                    )
                    
                    # レスポンスからイベントを取得
                    if isinstance(response, dict):
                        events = response.get("events", [])
                        next_token = response.get("nextToken")
                    elif isinstance(response, list):
                        events = response
                        next_token = None
                    else:
                        events = []
                        next_token = None
                    
                    all_events.extend(events)
                    
                    # 次のページがない場合は終了
                    if not next_token:
                        break
                
                return all_events
            
            # タイムアウト付きで実行（30秒、要件 3.5）
            try:
                all_events = self._execute_with_timeout(
                    fetch_all_events,
                    timeout_seconds=30,
                    operation_name="統計情報取得"
                )
            except TimeoutError as e:
                yield self.create_text_message(f"⏱️ {str(e)}\nデータ量が多い場合は、時間範囲を狭めて再試行してください。")
                return
            
            # イベントデータを集計（要件 2.1, 2.2, 2.3）
            aggregated = self._aggregate_events(all_events)
            
            # ストレージサイズを計算（要件 2.4）
            storage_size_bytes = self._calculate_storage_size(all_events)
            storage_size_mb = round(storage_size_bytes / (1024 * 1024), 2)
            
            # 統計情報を構築
            statistics = {
                "event_count": aggregated["event_count"],
                "storage_size_bytes": storage_size_bytes,
                "storage_size_mb": storage_size_mb,
                "actor_count": aggregated["actor_count"],
                "session_count": aggregated["session_count"],
                "first_event_timestamp": aggregated["first_event_timestamp"],
                "last_event_timestamp": aggregated["last_event_timestamp"],
            }
            
            # メトリクスフィルターの適用
            if metrics:
                filtered_statistics = {}
                for metric in metrics:
                    if metric in statistics:
                        filtered_statistics[metric] = statistics[metric]
                statistics = filtered_statistics
            
            # レスポンスデータを構築（要件 12.1）
            response_data = {
                "message": "Memory 統計情報を取得しました",
                "data": {
                    "memory_id": memory_id,
                    "statistics": statistics,
                    "cached": False,
                    "generated_at": datetime.now(timezone.utc).isoformat()
                }
            }
            
            # キャッシュに保存（要件 3.2）
            if use_cache:
                self._set_to_cache(cache_key, response_data)
            
            yield self.create_json_message(response_data)
            
            # サマリーメッセージ
            yield self.create_text_message(
                f"✅ Memory 統計情報の取得が完了しました\n\n"
                f"イベント数: {statistics.get('event_count', 0)}件\n"
                f"ストレージサイズ: {statistics.get('storage_size_mb', 0)} MB\n"
                f"Actor 数: {statistics.get('actor_count', 0)}人\n"
                f"Session 数: {statistics.get('session_count', 0)}件"
            )
            
        except Exception as exc:
            logger.error(f"Get statistics error: {exc}", exc_info=True)
            
            # ClientError の場合は適切なエラーメッセージを生成
            from botocore.exceptions import ClientError
            if isinstance(exc, ClientError):
                error_message = AgentCoreError.handle_client_error(exc, context)
            else:
                error_message = f"統計情報の取得中に予期しないエラーが発生しました: {exc}"
            
            yield self.create_text_message(f"❌ {error_message}")

    # ------------------------------------------------------------------
    # Actor 別統計情報取得
    # ------------------------------------------------------------------
    def _get_actor_statistics(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        Actor 別の統計情報を取得する。
        
        Args:
            tool_parameters: ツールパラメータ（memory_id, top_n, sort_by, start_time, end_time を含む）
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
            
        要件: 1.2, 2.1, 2.2
        """
        memory_id = tool_parameters.get("memory_id", "").strip()
        if not memory_id:
            yield self.create_text_message("❌ Memory ID は必須です")
            return
        
        # パラメータの取得
        top_n = tool_parameters.get("top_n", 10)
        sort_by = tool_parameters.get("sort_by", "event_count")
        start_time = tool_parameters.get("start_time")
        end_time = tool_parameters.get("end_time")
        
        # sort_by の検証
        valid_sort_by = {"event_count", "last_activity"}
        if sort_by not in valid_sort_by:
            yield self.create_text_message(
                f"❌ 無効な sort_by パラメータです: {sort_by}\n"
                f"有効な値: {', '.join(valid_sort_by)}"
            )
            return
        
        # エラーコンテキストの作成
        context = AgentCoreError.create_error_context(
            operation="get_actor_statistics",
            memory_id=memory_id
        )
        
        yield self.create_text_message(f"👥 Actor 別統計情報を取得中: {memory_id}")
        
        try:
            # イベント一覧を取得（ページネーション対応）
            all_events = []
            next_token = None
            max_results_per_page = 100
            
            # 時間範囲のフィルタリング用にタイムスタンプを解析
            start_timestamp = None
            end_timestamp = None
            if start_time:
                try:
                    start_timestamp = TimeUtils.parse_time_input(start_time)
                except Exception as e:
                    yield self.create_text_message(f"❌ start_time の解析に失敗しました: {e}")
                    return
            
            if end_time:
                try:
                    end_timestamp = TimeUtils.parse_time_input(end_time)
                except Exception as e:
                    yield self.create_text_message(f"❌ end_time の解析に失敗しました: {e}")
                    return
            
            # イベント一覧を取得（ページネーション対応）
            # タイムアウト付きで実行（要件 3.5）
            def fetch_all_events():
                all_events = []
                next_token = None
                max_results_per_page = 100
                
                while True:
                    request_params = {
                        "memory_id": memory_id,
                        "max_results": max_results_per_page
                    }
                    
                    if next_token:
                        request_params["next_token"] = next_token
                    
                    # list_events を呼び出し（リトライロジック付き、要件 10.4）
                    response = self._execute_with_retry(
                        lambda: self.memory_client.list_events(**request_params),
                        operation_name="list_events"
                    )
                    
                    # レスポンスからイベントを取得
                    if isinstance(response, dict):
                        events = response.get("events", [])
                        next_token = response.get("nextToken")
                    elif isinstance(response, list):
                        events = response
                        next_token = None
                    else:
                        events = []
                        next_token = None
                    
                    all_events.extend(events)
                    
                    # 次のページがない場合は終了
                    if not next_token:
                        break
                
                return all_events
            
            # タイムアウト付きで実行（30秒、要件 3.5）
            try:
                all_events = self._execute_with_timeout(
                    fetch_all_events,
                    timeout_seconds=30,
                    operation_name="Actor別統計情報取得"
                )
            except TimeoutError as e:
                yield self.create_text_message(f"⏱️ {str(e)}\nデータ量が多い場合は、時間範囲を狭めて再試行してください。")
                return
            
            # 時間範囲でフィルタリング（要件 2.5）
            if start_timestamp or end_timestamp:
                filtered_events = []
                for event in all_events:
                    event_timestamp = event.get("timestamp")
                    if event_timestamp:
                        # タイムスタンプを比較可能な形式に変換
                        try:
                            event_time = datetime.fromisoformat(event_timestamp.replace('Z', '+00:00'))
                            
                            # 時間範囲チェック
                            if start_timestamp and event_time < start_timestamp:
                                continue
                            if end_timestamp and event_time > end_timestamp:
                                continue
                            
                            filtered_events.append(event)
                        except Exception:
                            # タイムスタンプの解析に失敗した場合はスキップ
                            continue
                
                all_events = filtered_events
            
            # Actor ごとにイベントをグループ化（要件 1.2, 2.1, 2.2）
            actor_data = defaultdict(lambda: {
                "event_ids": set(),
                "session_ids": set(),
                "message_count": 0,
                "first_activity": None,
                "last_activity": None
            })
            
            for event in all_events:
                actor_id = event.get("actorId")
                if not actor_id:
                    continue
                
                event_id = event.get("eventId")
                session_id = event.get("sessionId")
                timestamp = event.get("timestamp")
                
                # イベント ID を追加（一意性を保証）
                if event_id:
                    actor_data[actor_id]["event_ids"].add(event_id)
                
                # Session ID を追加
                if session_id:
                    actor_data[actor_id]["session_ids"].add(session_id)
                
                # メッセージ数をカウント（イベントタイプが message の場合）
                event_type = event.get("eventType", "").lower()
                if "message" in event_type or event.get("content"):
                    actor_data[actor_id]["message_count"] += 1
                
                # 最初と最後のアクティビティ時刻を追跡
                if timestamp:
                    if actor_data[actor_id]["first_activity"] is None or timestamp < actor_data[actor_id]["first_activity"]:
                        actor_data[actor_id]["first_activity"] = timestamp
                    if actor_data[actor_id]["last_activity"] is None or timestamp > actor_data[actor_id]["last_activity"]:
                        actor_data[actor_id]["last_activity"] = timestamp
            
            # Actor 統計情報のリストを構築
            actor_statistics = []
            for actor_id, data in actor_data.items():
                actor_statistics.append({
                    "actor_id": actor_id,
                    "event_count": len(data["event_ids"]),
                    "session_count": len(data["session_ids"]),
                    "message_count": data["message_count"],
                    "first_activity": data["first_activity"],
                    "last_activity": data["last_activity"]
                })
            
            # ソート（要件 1.2）
            if sort_by == "event_count":
                actor_statistics.sort(key=lambda x: x["event_count"], reverse=True)
            elif sort_by == "last_activity":
                actor_statistics.sort(
                    key=lambda x: x["last_activity"] if x["last_activity"] else "",
                    reverse=True
                )
            
            # top_n で制限（要件 1.2）
            total_actors = len(actor_statistics)
            actor_statistics = actor_statistics[:top_n]
            
            # レスポンスデータを構築（要件 12.1）
            response_data = {
                "message": f"Actor 別統計情報を取得しました（全{total_actors}件中{len(actor_statistics)}件）",
                "data": {
                    "memory_id": memory_id,
                    "top_n": top_n,
                    "sort_by": sort_by,
                    "total_actors": total_actors,
                    "actors": actor_statistics,
                    "time_range": {
                        "start_time": start_time,
                        "end_time": end_time
                    } if start_time or end_time else None,
                    "generated_at": datetime.now(timezone.utc).isoformat()
                }
            }
            
            yield self.create_json_message(response_data)
            
            # サマリーメッセージ
            if actor_statistics:
                top_actor = actor_statistics[0]
                yield self.create_text_message(
                    f"✅ Actor 別統計情報の取得が完了しました\n\n"
                    f"総 Actor 数: {total_actors}人\n"
                    f"表示件数: {len(actor_statistics)}件\n"
                    f"最も活発な Actor: {top_actor['actor_id']}\n"
                    f"  - イベント数: {top_actor['event_count']}件\n"
                    f"  - Session 数: {top_actor['session_count']}件\n"
                    f"  - メッセージ数: {top_actor['message_count']}件"
                )
            else:
                yield self.create_text_message(
                    f"✅ Actor 別統計情報の取得が完了しました\n\n"
                    f"指定された条件に一致する Actor が見つかりませんでした。"
                )
            
        except Exception as exc:
            logger.error(f"Get actor statistics error: {exc}", exc_info=True)
            
            # ClientError の場合は適切なエラーメッセージを生成
            from botocore.exceptions import ClientError
            if isinstance(exc, ClientError):
                error_message = AgentCoreError.handle_client_error(exc, context)
            else:
                error_message = f"Actor 別統計情報の取得中に予期しないエラーが発生しました: {exc}"
            
            yield self.create_text_message(f"❌ {error_message}")

    # ------------------------------------------------------------------
    # Session 別統計情報取得
    # ------------------------------------------------------------------
    def _get_session_statistics(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        Session 別の統計情報を取得する。
        
        Args:
            tool_parameters: ツールパラメータ（memory_id, actor_id, top_n, sort_by, start_time, end_time を含む）
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
            
        要件: 1.3, 2.1, 2.3
        """
        memory_id = tool_parameters.get("memory_id", "").strip()
        if not memory_id:
            yield self.create_text_message("❌ Memory ID は必須です")
            return
        
        # パラメータの取得
        actor_id = tool_parameters.get("actor_id")
        top_n = tool_parameters.get("top_n", 20)
        sort_by = tool_parameters.get("sort_by", "event_count")
        start_time = tool_parameters.get("start_time")
        end_time = tool_parameters.get("end_time")
        
        # sort_by の検証
        valid_sort_by = {"event_count", "message_count", "last_activity"}
        if sort_by not in valid_sort_by:
            yield self.create_text_message(
                f"❌ 無効な sort_by パラメータです: {sort_by}\n"
                f"有効な値: {', '.join(valid_sort_by)}"
            )
            return
        
        # エラーコンテキストの作成
        context = AgentCoreError.create_error_context(
            operation="get_session_statistics",
            memory_id=memory_id
        )
        
        yield self.create_text_message(f"💬 Session 別統計情報を取得中: {memory_id}")
        
        try:
            # イベント一覧を取得（ページネーション対応）
            all_events = []
            next_token = None
            max_results_per_page = 100
            
            # 時間範囲のフィルタリング用にタイムスタンプを解析（Unix timestamp ms）
            start_timestamp_ms = None
            end_timestamp_ms = None
            if start_time:
                try:
                    start_timestamp_ms = TimeUtils.parse_time_input(start_time)
                except Exception as e:
                    yield self.create_text_message(f"❌ start_time の解析に失敗しました: {e}")
                    return
            
            if end_time:
                try:
                    end_timestamp_ms = TimeUtils.parse_time_input(end_time)
                except Exception as e:
                    yield self.create_text_message(f"❌ end_time の解析に失敗しました: {e}")
                    return
            
            # イベント一覧を取得（ページネーション対応）
            # タイムアウト付きで実行（要件 3.5）
            def fetch_all_events():
                all_events = []
                next_token = None
                max_results_per_page = 100
                
                while True:
                    request_params = {
                        "memory_id": memory_id,
                        "max_results": max_results_per_page
                    }
                    
                    if next_token:
                        request_params["next_token"] = next_token
                    
                    # list_events を呼び出し（リトライロジック付き、要件 10.4）
                    response = self._execute_with_retry(
                        lambda: self.memory_client.list_events(**request_params),
                        operation_name="list_events"
                    )
                    
                    # レスポンスからイベントを取得
                    if isinstance(response, dict):
                        events = response.get("events", [])
                        next_token = response.get("nextToken")
                    elif isinstance(response, list):
                        events = response
                        next_token = None
                    else:
                        events = []
                        next_token = None
                    
                    all_events.extend(events)
                    
                    # 次のページがない場合は終了
                    if not next_token:
                        break
                
                return all_events
            
            # タイムアウト付きで実行（30秒、要件 3.5）
            try:
                all_events = self._execute_with_timeout(
                    fetch_all_events,
                    timeout_seconds=30,
                    operation_name="Session別統計情報取得"
                )
            except TimeoutError as e:
                yield self.create_text_message(f"⏱️ {str(e)}\nデータ量が多い場合は、時間範囲を狭めて再試行してください。")
                return
            
            # 時間範囲でフィルタリング（要件 2.5）
            if start_timestamp_ms or end_timestamp_ms:
                filtered_events = []
                for event in all_events:
                    event_timestamp = event.get("timestamp")
                    if event_timestamp:
                        # タイムスタンプを Unix timestamp ms に変換
                        try:
                            # ISO 8601 形式のタイムスタンプを datetime に変換
                            if isinstance(event_timestamp, str):
                                event_time = datetime.fromisoformat(event_timestamp.replace('Z', '+00:00'))
                                event_time_ms = int(event_time.timestamp() * 1000)
                            elif isinstance(event_timestamp, int):
                                event_time_ms = event_timestamp
                            else:
                                continue
                            
                            # 時間範囲チェック
                            if start_timestamp_ms and event_time_ms < start_timestamp_ms:
                                continue
                            
                            if end_timestamp_ms and event_time_ms > end_timestamp_ms:
                                continue
                            
                            filtered_events.append(event)
                        except Exception as e:
                            # タイムスタンプの解析に失敗した場合はスキップ
                            logger.debug(f"Failed to parse timestamp: {event_timestamp}, error: {e}")
                            continue
                
                all_events = filtered_events
            
            # Actor ID でフィルタリング（オプション）
            if actor_id:
                all_events = [event for event in all_events if event.get("actorId") == actor_id]
            
            # Session ごとにイベントをグループ化（要件 1.3, 2.1, 2.3）
            session_data = defaultdict(lambda: {
                "actor_id": None,
                "event_ids": set(),
                "message_count": 0,
                "first_activity": None,
                "last_activity": None
            })
            
            for event in all_events:
                session_id = event.get("sessionId")
                if not session_id:
                    continue
                
                event_id = event.get("eventId")
                event_actor_id = event.get("actorId")
                timestamp = event.get("timestamp")
                
                # Actor ID を設定（最初に見つかった Actor ID を使用）
                if not session_data[session_id]["actor_id"] and event_actor_id:
                    session_data[session_id]["actor_id"] = event_actor_id
                
                # イベント ID を追加（一意性を保証）
                if event_id:
                    session_data[session_id]["event_ids"].add(event_id)
                
                # メッセージ数をカウント（イベントタイプが message の場合）
                event_type = event.get("eventType", "").lower()
                if "message" in event_type or event.get("content"):
                    session_data[session_id]["message_count"] += 1
                
                # 最初と最後のアクティビティ時刻を追跡
                if timestamp:
                    if session_data[session_id]["first_activity"] is None or timestamp < session_data[session_id]["first_activity"]:
                        session_data[session_id]["first_activity"] = timestamp
                    if session_data[session_id]["last_activity"] is None or timestamp > session_data[session_id]["last_activity"]:
                        session_data[session_id]["last_activity"] = timestamp
            
            # Session 統計情報のリストを構築
            session_statistics = []
            for session_id, data in session_data.items():
                # Session の継続時間を計算（要件 1.3）
                duration_seconds = 0
                if data["first_activity"] and data["last_activity"]:
                    try:
                        first_time = datetime.fromisoformat(data["first_activity"].replace('Z', '+00:00'))
                        last_time = datetime.fromisoformat(data["last_activity"].replace('Z', '+00:00'))
                        duration_seconds = int((last_time - first_time).total_seconds())
                    except Exception:
                        duration_seconds = 0
                
                session_statistics.append({
                    "session_id": session_id,
                    "actor_id": data["actor_id"],
                    "event_count": len(data["event_ids"]),
                    "message_count": data["message_count"],
                    "first_activity": data["first_activity"],
                    "last_activity": data["last_activity"],
                    "duration_seconds": duration_seconds
                })
            
            # ソート（要件 1.3）
            if sort_by == "event_count":
                session_statistics.sort(key=lambda x: x["event_count"], reverse=True)
            elif sort_by == "message_count":
                session_statistics.sort(key=lambda x: x["message_count"], reverse=True)
            elif sort_by == "last_activity":
                session_statistics.sort(
                    key=lambda x: x["last_activity"] if x["last_activity"] else "",
                    reverse=True
                )
            
            # top_n で制限（要件 1.3）
            total_sessions = len(session_statistics)
            session_statistics = session_statistics[:top_n]
            
            # レスポンスデータを構築（要件 12.1）
            response_data = {
                "message": f"Session 別統計情報を取得しました（全{total_sessions}件中{len(session_statistics)}件）",
                "data": {
                    "memory_id": memory_id,
                    "actor_id": actor_id,
                    "top_n": top_n,
                    "sort_by": sort_by,
                    "total_sessions": total_sessions,
                    "sessions": session_statistics,
                    "time_range": {
                        "start_time": start_time,
                        "end_time": end_time
                    } if start_time or end_time else None,
                    "generated_at": datetime.now(timezone.utc).isoformat()
                }
            }
            
            yield self.create_json_message(response_data)
            
            # サマリーメッセージ
            if session_statistics:
                top_session = session_statistics[0]
                yield self.create_text_message(
                    f"✅ Session 別統計情報の取得が完了しました\n\n"
                    f"総 Session 数: {total_sessions}件\n"
                    f"表示件数: {len(session_statistics)}件\n"
                    f"最も活発な Session: {top_session['session_id']}\n"
                    f"  - イベント数: {top_session['event_count']}件\n"
                    f"  - メッセージ数: {top_session['message_count']}件\n"
                    f"  - 継続時間: {top_session['duration_seconds']}秒"
                )
            else:
                yield self.create_text_message(
                    f"✅ Session 別統計情報の取得が完了しました\n\n"
                    f"指定された条件に一致する Session が見つかりませんでした。"
                )
            
        except Exception as exc:
            logger.error(f"Get session statistics error: {exc}", exc_info=True)
            
            # ClientError の場合は適切なエラーメッセージを生成
            from botocore.exceptions import ClientError
            if isinstance(exc, ClientError):
                error_message = AgentCoreError.handle_client_error(exc, context)
            else:
                error_message = f"Session 別統計情報の取得中に予期しないエラーが発生しました: {exc}"
            
            yield self.create_text_message(f"❌ {error_message}")

    # ------------------------------------------------------------------
    # 時系列推移取得
    # ------------------------------------------------------------------
    def _get_timeline(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        時系列でのイベント数推移を取得する。
        
        Args:
            tool_parameters: ツールパラメータ（memory_id, start_time, end_time, granularity, metric, actor_id を含む）
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
            
        要件: 1.4, 2.5
        """
        memory_id = tool_parameters.get("memory_id", "").strip()
        if not memory_id:
            yield self.create_text_message("❌ Memory ID は必須です")
            return
        
        # 必須パラメータの検証
        start_time = tool_parameters.get("start_time")
        end_time = tool_parameters.get("end_time")
        granularity = tool_parameters.get("granularity", "").strip().lower()
        
        if not start_time:
            yield self.create_text_message("❌ start_time は必須です")
            return
        
        if not end_time:
            yield self.create_text_message("❌ end_time は必須です")
            return
        
        if not granularity:
            yield self.create_text_message("❌ granularity は必須です")
            return
        
        # granularity の検証（要件 1.4）
        valid_granularities = {"hour", "day", "week", "month"}
        if granularity not in valid_granularities:
            yield self.create_text_message(
                f"❌ 無効な granularity パラメータです: {granularity}\n"
                f"有効な値: {', '.join(valid_granularities)}"
            )
            return
        
        # オプションパラメータの取得
        metric = tool_parameters.get("metric", "event_count")
        actor_id = tool_parameters.get("actor_id")
        
        # エラーコンテキストの作成
        context = AgentCoreError.create_error_context(
            operation="get_timeline",
            memory_id=memory_id
        )
        
        yield self.create_text_message(f"📈 時系列推移を取得中: {memory_id}")
        
        try:
            # 時間範囲パラメータを解析（TimeUtils を使用、要件 1.4）
            try:
                start_timestamp_ms = TimeUtils.parse_time_input(start_time)
                end_timestamp_ms = TimeUtils.parse_time_input(end_time)
            except Exception as e:
                yield self.create_text_message(f"❌ 時間範囲の解析に失敗しました: {e}")
                return
            
            # 時間範囲の妥当性を検証
            is_valid, error_msg = TimeUtils.validate_time_range(start_timestamp_ms, end_timestamp_ms)
            if not is_valid:
                yield self.create_text_message(f"❌ {error_msg}")
                return
            
            # イベント一覧を取得（ページネーション対応）
            # タイムアウト付きで実行（要件 3.5）
            def fetch_all_events():
                all_events = []
                next_token = None
                max_results_per_page = 100
                
                while True:
                    request_params = {
                        "memory_id": memory_id,
                        "max_results": max_results_per_page
                    }
                    
                    if next_token:
                        request_params["next_token"] = next_token
                    
                    # list_events を呼び出し（リトライロジック付き、要件 10.4）
                    response = self._execute_with_retry(
                        lambda: self.memory_client.list_events(**request_params),
                        operation_name="list_events"
                    )
                    
                    # レスポンスからイベントを取得
                    if isinstance(response, dict):
                        events = response.get("events", [])
                        next_token = response.get("nextToken")
                    elif isinstance(response, list):
                        events = response
                        next_token = None
                    else:
                        events = []
                        next_token = None
                    
                    all_events.extend(events)
                    
                    # 次のページがない場合は終了
                    if not next_token:
                        break
                
                return all_events
            
            # タイムアウト付きで実行（30秒、要件 3.5）
            try:
                all_events = self._execute_with_timeout(
                    fetch_all_events,
                    timeout_seconds=30,
                    operation_name="時系列推移取得"
                )
            except TimeoutError as e:
                yield self.create_text_message(f"⏱️ {str(e)}\nデータ量が多い場合は、時間範囲を狭めて再試行してください。")
                return
            
            # 時間範囲でフィルタリング（要件 2.5）
            filtered_events = []
            for event in all_events:
                event_timestamp = event.get("timestamp")
                if event_timestamp:
                    try:
                        # タイムスタンプを Unix timestamp ms に変換
                        if isinstance(event_timestamp, str):
                            event_time = datetime.fromisoformat(event_timestamp.replace('Z', '+00:00'))
                            event_time_ms = int(event_time.timestamp() * 1000)
                        elif isinstance(event_timestamp, int):
                            event_time_ms = event_timestamp
                        else:
                            continue
                        
                        # 時間範囲チェック（要件 2.5）
                        if event_time_ms < start_timestamp_ms or event_time_ms > end_timestamp_ms:
                            continue
                        
                        filtered_events.append(event)
                    except Exception as e:
                        logger.debug(f"Failed to parse timestamp: {event_timestamp}, error: {e}")
                        continue
            
            # Actor ID でフィルタリング（オプション）
            if actor_id:
                filtered_events = [event for event in filtered_events if event.get("actorId") == actor_id]
            
            # 粒度に応じてデータを集計（要件 1.4）
            timeline_data = self._aggregate_timeline_data(
                filtered_events,
                start_timestamp_ms,
                end_timestamp_ms,
                granularity
            )
            
            # タイムスタンプを人間が読みやすい形式に変換（要件 1.4）
            formatted_timeline = []
            for data_point in timeline_data:
                formatted_timeline.append({
                    "timestamp": data_point["timestamp"],
                    "timestamp_readable": self._format_timestamp_readable(data_point["timestamp_ms"], granularity),
                    "event_count": data_point["event_count"],
                    "actor_count": data_point["actor_count"],
                    "session_count": data_point["session_count"]
                })
            
            # レスポンスデータを構築（要件 12.1）
            response_data = {
                "message": f"時系列推移を取得しました（{len(formatted_timeline)}データポイント）",
                "data": {
                    "memory_id": memory_id,
                    "time_range": {
                        "start_time": start_time,
                        "end_time": end_time,
                        "start_timestamp_ms": start_timestamp_ms,
                        "end_timestamp_ms": end_timestamp_ms
                    },
                    "granularity": granularity,
                    "metric": metric,
                    "actor_id": actor_id,
                    "timeline": formatted_timeline,
                    "summary": {
                        "total_events": sum(dp["event_count"] for dp in formatted_timeline),
                        "total_actors": len(set(
                            event.get("actorId")
                            for event in filtered_events
                            if event.get("actorId")
                        )),
                        "total_sessions": len(set(
                            event.get("sessionId")
                            for event in filtered_events
                            if event.get("sessionId")
                        )),
                        "data_points": len(formatted_timeline)
                    },
                    "generated_at": datetime.now(timezone.utc).isoformat()
                }
            }
            
            yield self.create_json_message(response_data)
            
            # サマリーメッセージ
            summary = response_data["data"]["summary"]
            yield self.create_text_message(
                f"✅ 時系列推移の取得が完了しました\n\n"
                f"期間: {TimeUtils.format_duration(start_timestamp_ms, end_timestamp_ms)}\n"
                f"粒度: {granularity}\n"
                f"データポイント数: {summary['data_points']}件\n"
                f"総イベント数: {summary['total_events']}件\n"
                f"総 Actor 数: {summary['total_actors']}人\n"
                f"総 Session 数: {summary['total_sessions']}件"
            )
            
        except Exception as exc:
            logger.error(f"Get timeline error: {exc}", exc_info=True)
            
            # ClientError の場合は適切なエラーメッセージを生成
            from botocore.exceptions import ClientError
            if isinstance(exc, ClientError):
                error_message = AgentCoreError.handle_client_error(exc, context)
            else:
                error_message = f"時系列推移の取得中に予期しないエラーが発生しました: {exc}"
            
            yield self.create_text_message(f"❌ {error_message}")
    
    def _aggregate_timeline_data(
        self,
        events: List[Dict[str, Any]],
        start_timestamp_ms: int,
        end_timestamp_ms: int,
        granularity: str
    ) -> List[Dict[str, Any]]:
        """
        イベントを時系列バケットに集計する。
        
        Args:
            events: イベントのリスト
            start_timestamp_ms: 開始時刻（Unix timestamp ms）
            end_timestamp_ms: 終了時刻（Unix timestamp ms）
            granularity: 粒度（hour, day, week, month）
            
        Returns:
            List[Dict[str, Any]]: 時系列データポイントのリスト
            
        要件: 1.4, 2.5
        """
        # 粒度に応じたバケットサイズ（ミリ秒）を計算
        bucket_size_ms = self._get_bucket_size_ms(granularity)
        
        # 時間バケットを生成
        buckets = {}
        current_bucket_start = self._align_to_bucket(start_timestamp_ms, granularity)
        
        while current_bucket_start <= end_timestamp_ms:
            bucket_key = current_bucket_start
            buckets[bucket_key] = {
                "timestamp_ms": current_bucket_start,
                "timestamp": TimeUtils.to_iso8601(current_bucket_start),
                "event_ids": set(),
                "actor_ids": set(),
                "session_ids": set()
            }
            current_bucket_start += bucket_size_ms
        
        # イベントを各バケットに振り分け（要件 1.4）
        for event in events:
            event_timestamp = event.get("timestamp")
            if not event_timestamp:
                continue
            
            try:
                # タイムスタンプを Unix timestamp ms に変換
                if isinstance(event_timestamp, str):
                    event_time = datetime.fromisoformat(event_timestamp.replace('Z', '+00:00'))
                    event_time_ms = int(event_time.timestamp() * 1000)
                elif isinstance(event_timestamp, int):
                    event_time_ms = event_timestamp
                else:
                    continue
                
                # イベントが属するバケットを特定
                bucket_key = self._align_to_bucket(event_time_ms, granularity)
                
                if bucket_key in buckets:
                    # イベント ID を追加（一意性を保証）
                    event_id = event.get("eventId")
                    if event_id:
                        buckets[bucket_key]["event_ids"].add(event_id)
                    
                    # Actor ID を追加
                    actor_id = event.get("actorId")
                    if actor_id:
                        buckets[bucket_key]["actor_ids"].add(actor_id)
                    
                    # Session ID を追加
                    session_id = event.get("sessionId")
                    if session_id:
                        buckets[bucket_key]["session_ids"].add(session_id)
            
            except Exception as e:
                logger.debug(f"Failed to process event timestamp: {event_timestamp}, error: {e}")
                continue
        
        # バケットデータを集計結果に変換（要件 1.4）
        timeline_data = []
        for bucket_key in sorted(buckets.keys()):
            bucket = buckets[bucket_key]
            timeline_data.append({
                "timestamp_ms": bucket["timestamp_ms"],
                "timestamp": bucket["timestamp"],
                "event_count": len(bucket["event_ids"]),
                "actor_count": len(bucket["actor_ids"]),
                "session_count": len(bucket["session_ids"])
            })
        
        return timeline_data
    
    def _get_bucket_size_ms(self, granularity: str) -> int:
        """
        粒度に応じたバケットサイズ（ミリ秒）を取得する。
        
        Args:
            granularity: 粒度（hour, day, week, month）
            
        Returns:
            int: バケットサイズ（ミリ秒）
            
        要件: 1.4
        """
        if granularity == "hour":
            return 3600 * 1000  # 1時間
        elif granularity == "day":
            return 86400 * 1000  # 1日
        elif granularity == "week":
            return 604800 * 1000  # 7日
        elif granularity == "month":
            return 2592000 * 1000  # 30日（簡略化）
        else:
            return 86400 * 1000  # デフォルトは1日
    
    def _align_to_bucket(self, timestamp_ms: int, granularity: str) -> int:
        """
        タイムスタンプを粒度に応じたバケットの開始時刻にアライメントする。
        
        Args:
            timestamp_ms: Unix timestamp（ミリ秒）
            granularity: 粒度（hour, day, week, month）
            
        Returns:
            int: アライメントされたタイムスタンプ（ミリ秒）
            
        要件: 1.4
        """
        dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        
        if granularity == "hour":
            # 時間の開始にアライメント
            aligned_dt = dt.replace(minute=0, second=0, microsecond=0)
        elif granularity == "day":
            # 日の開始にアライメント
            aligned_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        elif granularity == "week":
            # 週の開始（月曜日）にアライメント
            days_since_monday = dt.weekday()
            aligned_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_since_monday)
        elif granularity == "month":
            # 月の開始にアライメント
            aligned_dt = dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            # デフォルトは日の開始
            aligned_dt = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        
        return int(aligned_dt.timestamp() * 1000)
    
    def _format_timestamp_readable(self, timestamp_ms: int, granularity: str) -> str:
        """
        タイムスタンプを人間が読みやすい形式に変換する。
        
        Args:
            timestamp_ms: Unix timestamp（ミリ秒）
            granularity: 粒度（hour, day, week, month）
            
        Returns:
            str: 人間が読みやすい形式のタイムスタンプ
            
        要件: 1.4
        """
        dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        
        if granularity == "hour":
            # 例: "2025-01-31 12:00 JST"
            dt_jst = dt.astimezone(timezone(timedelta(hours=9)))
            return dt_jst.strftime("%Y-%m-%d %H:%M JST")
        elif granularity == "day":
            # 例: "2025-01-31"
            return dt.strftime("%Y-%m-%d")
        elif granularity == "week":
            # 例: "2025-W05 (2025-01-27)"
            week_number = dt.isocalendar()[1]
            return f"{dt.year}-W{week_number:02d} ({dt.strftime('%Y-%m-%d')})"
        elif granularity == "month":
            # 例: "2025-01"
            return dt.strftime("%Y-%m")
        else:
            # デフォルトは ISO 8601 形式
            return TimeUtils.to_iso8601(timestamp_ms)

    # ------------------------------------------------------------------
    # Dify Tool エントリ
    # ------------------------------------------------------------------
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        """
        Memory 統計情報取得操作のメインエントリポイント。
        
        Args:
            tool_parameters: ツールパラメータ（operation を含む）
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
            
        要件: 1.1, 1.2, 1.3, 1.4
        """
        operation = tool_parameters.get("operation")
        
        # 操作の検証
        valid_operations = {"get_statistics", "get_actor_statistics", "get_session_statistics", "get_timeline"}
        if operation not in valid_operations:
            yield self.create_text_message(
                f"❌ 無効な操作です: {operation}\n"
                f"有効な操作: {', '.join(valid_operations)}"
            )
            return
        
        # Memory クライアントの初期化
        if not self.memory_client:
            success, error_msg = self._initialize_memory_client(tool_parameters)
            if not success:
                yield self.create_text_message(error_msg)
                return
        
        # 操作のルーティング（要件 1.1, 1.2, 1.3, 1.4）
        if operation == "get_statistics":
            yield from self._get_statistics(tool_parameters)
        elif operation == "get_actor_statistics":
            yield from self._get_actor_statistics(tool_parameters)
        elif operation == "get_session_statistics":
            yield from self._get_session_statistics(tool_parameters)
        elif operation == "get_timeline":
            yield from self._get_timeline(tool_parameters)
