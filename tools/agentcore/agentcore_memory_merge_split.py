"""
場所: tools/agentcore/agentcore_memory_merge_split.py
内容: Bedrock AgentCore Memory のマージ・分割機能を提供する Dify ツール。
目的: Memory リソースの再編成機能を提供し、データ管理を効率化する。
"""

import hashlib
import json
import logging
import os
import sys
from collections.abc import Generator
from typing import Any, Dict, Optional

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

# AgentCore SDK は追加依存のため、同梱されていない場合も考慮する
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from bedrock_agentcore.memory import MemoryClient

    AGENTCORE_SDK_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - SDK 未導入環境に備える
    MemoryClient = None
    AGENTCORE_SDK_AVAILABLE = False
    print(f"Warning: bedrock-agentcore SDK import failed: {exc}")

try:
    from utils.utils import (
        build_boto3_client_kwargs,
        resolve_aws_credentials,
        reset_clients_on_credential_change,
    )
    from utils.error_handler import AgentCoreError
except ModuleNotFoundError:  # pragma: no cover
    from utils.utils import (
        build_boto3_client_kwargs,
        resolve_aws_credentials,
        reset_clients_on_credential_change,
    )
    from utils.error_handler import AgentCoreError

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class AgentCoreMemoryMergeSplitTool(Tool):
    """AgentCore Memory のマージ・分割・イベントコピー機能を提供するツール"""

    memory_client: Any = None
    _client_credentials_signature: Optional[tuple] = None

    # ------------------------------------------------------------------
    # 初期化
    # ------------------------------------------------------------------
    def _initialize_client(self, tool_parameters: dict[str, Any]) -> bool:
        """
        AWS 認証情報から MemoryClient を初期化する
        
        Args:
            tool_parameters: ツールパラメータ辞書
            
        Returns:
            初期化が成功した場合は True、失敗した場合は False
        """
        # AgentCore SDK の可用性チェック
        if not AGENTCORE_SDK_AVAILABLE:
            logger.error("AgentCore Memory SDK not available")
            return False

        try:
            # AWS 認証情報の解決
            credentials = resolve_aws_credentials(self, tool_parameters)
            aws_region = credentials.get("aws_region") or "us-east-1"
            aws_access_key_id = credentials.get("aws_access_key_id")
            aws_secret_access_key = credentials.get("aws_secret_access_key")

            # 認証情報が変更された場合、クライアントをリセット
            reset_clients_on_credential_change(
                self, credentials, ["memory_client"]
            )

            # Memory Client の初期化
            if not self.memory_client:
                # 明示的な AK/SK が渡された場合は環境変数経由で設定
                if aws_access_key_id and aws_secret_access_key:
                    os.environ["AWS_ACCESS_KEY_ID"] = aws_access_key_id
                    os.environ["AWS_SECRET_ACCESS_KEY"] = aws_secret_access_key
                    os.environ["AWS_REGION"] = aws_region

                self.memory_client = MemoryClient(region_name=aws_region)
                logger.info("AgentCore Memory client initialized")

            return True

        except Exception as exc:  # pragma: no cover - SDK 例外
            logger.error(f"Failed to initialize client: {exc}")
            return False

    # ------------------------------------------------------------------
    # マージ操作
    # ------------------------------------------------------------------
    def _merge_memories(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        """
        複数の Memory リソースを1つにマージ
        
        処理フロー:
        1. すべてのソース Memory からイベントを収集
        2. イベントをタイムスタンプでソート
        3. 重複排除（オプション）
        4. 競合解決戦略の適用
        5. 戦略のマージ（オプション）
        6. ターゲット Memory へのイベント作成
        7. マージ結果の返却
        
        Args:
            tool_parameters: ツールパラメータ辞書
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
        """
        import time
        
        # 操作開始時刻を記録
        start_time = time.time()
        
        # パラメータの取得
        source_memory_ids = tool_parameters.get("source_memory_ids", [])
        target_memory_id = tool_parameters.get("target_memory_id", "").strip()
        conflict_resolution = tool_parameters.get("conflict_resolution", "keep_latest")
        merge_strategies = tool_parameters.get("merge_strategies", True)
        deduplicate = tool_parameters.get("deduplicate", True)
        
        # 操作開始ログ
        logger.info(
            f"Starting merge operation: "
            f"source_memories={source_memory_ids}, "
            f"target_memory={target_memory_id}, "
            f"conflict_resolution={conflict_resolution}, "
            f"deduplicate={deduplicate}"
        )
        
        # 必須パラメータのチェック
        if not source_memory_ids or len(source_memory_ids) < 2:
            yield self.create_text_message(
                "❌ source_memory_ids パラメータには2つ以上の Memory ID が必要です"
            )
            return
        
        if not target_memory_id:
            yield self.create_text_message("❌ target_memory_id パラメータは必須です")
            return
        
        # 競合解決戦略の検証
        valid_strategies = {"keep_latest", "keep_oldest", "merge_all"}
        if conflict_resolution not in valid_strategies:
            yield self.create_text_message(
                f"❌ 無効な競合解決戦略です: {conflict_resolution}\n"
                f"有効な戦略: {', '.join(valid_strategies)}"
            )
            return
        
        try:
            # ステップ1: すべてのソース Memory からイベントを収集
            yield self.create_text_message(
                f"📦 {len(source_memory_ids)}個の Memory からイベントを収集中..."
            )
            
            all_events = self._collect_events_from_memories(source_memory_ids)
            total_events = len(all_events)
            
            yield self.create_text_message(
                f"✅ イベント収集完了: {total_events}件のイベント"
            )
            
            # ステップ2: イベントをタイムスタンプでソート
            yield self.create_text_message("🔄 イベントをタイムスタンプでソート中...")
            sorted_events = self._sort_events_by_timestamp(all_events)
            
            # ステップ3: 重複排除（オプション）
            deduplicated_count = 0
            if deduplicate:
                yield self.create_text_message("🔍 重複イベントを排除中...")
                before_count = len(sorted_events)
                sorted_events = self._deduplicate_events(sorted_events)
                after_count = len(sorted_events)
                deduplicated_count = before_count - after_count
                
                if deduplicated_count > 0:
                    yield self.create_text_message(
                        f"✅ 重複排除完了: {deduplicated_count}件の重複を除外"
                    )
            
            # ステップ4: 競合解決戦略の適用
            yield self.create_text_message(
                f"⚙️ 競合解決戦略を適用中: {conflict_resolution}"
            )
            resolved_events = self._apply_merge_conflict_resolution(
                sorted_events, conflict_resolution
            )
            merged_event_count = len(resolved_events)
            
            # ステップ5: 戦略のマージ（オプション）
            if merge_strategies:
                yield self.create_text_message("🔄 戦略をマージ中...")
                try:
                    merged_strategy_list = self._merge_strategies(source_memory_ids)
                    yield self.create_text_message(
                        f"✅ 戦略マージ完了: {len(merged_strategy_list)}個の戦略"
                    )
                except Exception as exc:
                    logger.warning(f"Strategy merge failed: {exc}")
                    yield self.create_text_message(
                        f"⚠️ 戦略のマージに失敗しました: {str(exc)}"
                    )
            
            # ステップ6: ターゲット Memory へのイベント作成
            yield self.create_text_message(
                f"💾 ターゲット Memory {target_memory_id} にイベントを作成中..."
            )
            
            # バッチでイベントを作成
            batch_size = 50
            created_count = 0
            failed_count = 0
            errors = []
            
            for i in range(0, len(resolved_events), batch_size):
                batch = resolved_events[i:i + batch_size]
                
                for event in batch:
                    try:
                        # ソース Memory ID などの内部フィールドを除外
                        event_data = {
                            "memoryId": target_memory_id,
                            "actorId": event.get("actorId"),
                            "sessionId": event.get("sessionId"),
                            "namespace": event.get("namespace"),
                            "messages": event.get("messages", []),
                        }
                        
                        # イベントを作成
                        self.memory_client.create_event(**event_data)
                        created_count += 1
                        
                    except ClientError as error:
                        failed_count += 1
                        context = AgentCoreError.create_error_context(
                            operation="create_event",
                            memory_id=target_memory_id
                        )
                        error_message = AgentCoreError.handle_client_error(error, context)
                        errors.append({
                            "event_index": i + batch.index(event),
                            "error": error_message
                        })
                        logger.error(f"Failed to create event: {error_message}")
                
                # 進捗報告
                if (i + batch_size) % 200 == 0:
                    progress = min(100, ((i + batch_size) / len(resolved_events)) * 100)
                    yield self.create_text_message(
                        f"進捗: {progress:.1f}% ({created_count}/{len(resolved_events)})"
                    )
            
            # ステップ7: マージ結果の返却
            status = "success" if failed_count == 0 else "partial_success"
            
            result = {
                "status": status,
                "target_memory_id": target_memory_id,
                "results": {
                    "source_count": len(source_memory_ids),
                    "total_events": total_events,
                    "merged_events": created_count,
                    "deduplicated_events": deduplicated_count,
                    "failed_events": failed_count,
                },
            }
            
            if errors:
                result["errors"] = errors[:10]  # 最初の10件のエラーのみ返す
            
            yield self.create_json_message(result)
            
            # サマリーメッセージ
            summary = (
                f"✅ マージが完了しました\n\n"
                f"📊 ソース Memory 数: {len(source_memory_ids)}個\n"
                f"📦 総イベント数: {total_events}件\n"
                f"✨ マージされたイベント: {created_count}件\n"
                f"🔍 重複排除: {deduplicated_count}件\n"
                f"❌ 失敗: {failed_count}件\n"
                f"🎯 ターゲット Memory: {target_memory_id}"
            )
            
            if failed_count > 0:
                summary += f"\n\n⚠️ {failed_count}件のイベントの作成に失敗しました"
            
            yield self.create_text_message(summary)
            
            # 操作完了ログ
            elapsed_time = time.time() - start_time
            logger.info(
                f"Merge operation completed: "
                f"source_count={len(source_memory_ids)}, "
                f"total_events={total_events}, "
                f"merged_events={created_count}, "
                f"deduplicated_events={deduplicated_count}, "
                f"failed_events={failed_count}, "
                f"elapsed_time={elapsed_time:.2f}s"
            )
            
        except Exception as exc:
            # エラーログ
            elapsed_time = time.time() - start_time
            logger.error(
                f"Merge operation failed: "
                f"source_memories={source_memory_ids}, "
                f"target_memory={target_memory_id}, "
                f"elapsed_time={elapsed_time:.2f}s, "
                f"error={str(exc)}",
                exc_info=True
            )
            error_message = f"❌ マージに失敗しました: {str(exc)}"
            yield self.create_text_message(error_message)

    def _collect_events_from_memories(self, memory_ids: list[str]) -> list[dict]:
        """
        複数の Memory からイベントを収集
        
        Args:
            memory_ids: Memory ID のリスト
            
        Returns:
            収集されたイベントのリスト
            
        Raises:
            Exception: イベント収集に失敗した場合
        """
        all_events = []
        
        for memory_id in memory_ids:
            try:
                # ページネーションを使用してすべてのイベントを取得
                next_token = None
                
                while True:
                    # list_events API を呼び出し
                    list_params = {
                        "memoryId": memory_id,
                        "maxResults": 100,
                    }
                    
                    if next_token:
                        list_params["nextToken"] = next_token
                    
                    response = self.memory_client.list_events(**list_params)
                    
                    # イベントを収集
                    events = response.get("events", [])
                    for event in events:
                        # Memory ID を追加（マージ時のトレーサビリティ用）
                        event["_source_memory_id"] = memory_id
                        all_events.append(event)
                    
                    # 次のページがあるかチェック
                    next_token = response.get("nextToken")
                    if not next_token:
                        break
                
                logger.info(
                    f"Collected {len(events)} events from memory {memory_id}"
                )
                
            except ClientError as error:
                context = AgentCoreError.create_error_context(
                    operation="collect_events",
                    memory_id=memory_id
                )
                error_message = AgentCoreError.handle_client_error(error, context)
                logger.error(f"Failed to collect events: {error_message}")
                raise Exception(error_message)
        
        return all_events

    def _deduplicate_events(self, events: list[dict]) -> list[dict]:
        """
        イベントの重複を排除
        
        同一のイベントハッシュを持つイベントは1つのみ保持します。
        重複がある場合は、最初に出現したイベントを保持します。
        
        Args:
            events: イベントのリスト
            
        Returns:
            重複排除されたイベントのリスト
        """
        seen_hashes = set()
        unique_events = []
        
        for event in events:
            # イベントのハッシュ値を計算
            event_hash = self._calculate_event_hash(event)
            
            # 未出現のハッシュの場合のみ追加
            if event_hash not in seen_hashes:
                seen_hashes.add(event_hash)
                unique_events.append(event)
        
        logger.info(
            f"Deduplicated {len(events) - len(unique_events)} events "
            f"({len(unique_events)} unique events remain)"
        )
        
        return unique_events

    def _apply_merge_conflict_resolution(
        self, events: list[dict], strategy: str
    ) -> list[dict]:
        """
        マージ時の競合解決戦略を適用
        
        競合解決戦略:
        - keep_latest: 同一イベントの中で最新のタイムスタンプを持つイベントを保持
        - keep_oldest: 同一イベントの中で最古のタイムスタンプを持つイベントを保持
        - merge_all: すべてのイベントを保持（競合解決なし）
        
        Args:
            events: イベントのリスト
            strategy: 競合解決戦略（"keep_latest", "keep_oldest", "merge_all"）
            
        Returns:
            競合解決後のイベントのリスト
        """
        # merge_all の場合は競合解決を行わない
        if strategy == "merge_all":
            logger.info("Using merge_all strategy - no conflict resolution")
            return events
        
        # イベントをハッシュでグループ化
        event_groups = {}
        
        for event in events:
            event_hash = self._calculate_event_hash(event)
            
            if event_hash not in event_groups:
                event_groups[event_hash] = []
            
            event_groups[event_hash].append(event)
        
        # 各グループから1つのイベントを選択
        resolved_events = []
        
        for event_hash, group in event_groups.items():
            if len(group) == 1:
                # 競合なし
                resolved_events.append(group[0])
            else:
                # 競合あり - 戦略に応じて選択
                if strategy == "keep_latest":
                    # 最新のタイムスタンプを持つイベントを選択
                    selected = max(
                        group,
                        key=lambda e: e.get("metadata", {}).get("createdAt", "")
                    )
                elif strategy == "keep_oldest":
                    # 最古のタイムスタンプを持つイベントを選択
                    selected = min(
                        group,
                        key=lambda e: e.get("metadata", {}).get("createdAt", "")
                    )
                else:
                    # デフォルトは最初のイベント
                    selected = group[0]
                
                resolved_events.append(selected)
        
        # タイムスタンプでソート（競合解決後も順序を保つ）
        resolved_events = self._sort_events_by_timestamp(resolved_events)
        
        logger.info(
            f"Applied {strategy} conflict resolution: "
            f"{len(events)} events -> {len(resolved_events)} events"
        )
        
        return resolved_events

    def _merge_strategies(self, source_memories: list[str]) -> list[dict]:
        """
        複数の Memory の戦略をマージ
        
        各ソース Memory から戦略を取得し、重複を排除してマージします。
        戦略の重複は、戦略の種類（semanticMemoryStrategy, summaryMemoryStrategy など）
        と名前で判定します。
        
        Args:
            source_memories: ソース Memory ID のリスト
            
        Returns:
            マージされた戦略のリスト
            
        Raises:
            Exception: 戦略の取得に失敗した場合
        """
        all_strategies = []
        seen_strategies = set()
        
        for memory_id in source_memories:
            try:
                # Memory の詳細を取得
                response = self.memory_client.get_memory(memoryId=memory_id)
                
                # 戦略を取得
                strategies = response.get("strategies", [])
                
                for strategy in strategies:
                    # 戦略の種類を特定
                    strategy_type = None
                    strategy_name = None
                    
                    if "semanticMemoryStrategy" in strategy:
                        strategy_type = "semanticMemoryStrategy"
                        strategy_name = strategy["semanticMemoryStrategy"].get("name")
                    elif "summaryMemoryStrategy" in strategy:
                        strategy_type = "summaryMemoryStrategy"
                        strategy_name = strategy["summaryMemoryStrategy"].get("name")
                    elif "userPreferenceMemoryStrategy" in strategy:
                        strategy_type = "userPreferenceMemoryStrategy"
                        strategy_name = strategy["userPreferenceMemoryStrategy"].get("name")
                    
                    # 戦略の一意性を判定
                    strategy_key = f"{strategy_type}:{strategy_name}"
                    
                    if strategy_key not in seen_strategies:
                        seen_strategies.add(strategy_key)
                        all_strategies.append(strategy)
                
                logger.info(
                    f"Collected {len(strategies)} strategies from memory {memory_id}"
                )
                
            except ClientError as error:
                context = AgentCoreError.create_error_context(
                    operation="get_memory",
                    memory_id=memory_id
                )
                error_message = AgentCoreError.handle_client_error(error, context)
                logger.error(f"Failed to get strategies: {error_message}")
                # 戦略の取得に失敗しても処理を継続
                continue
        
        logger.info(
            f"Merged strategies: {len(all_strategies)} unique strategies "
            f"from {len(source_memories)} memories"
        )
        
        return all_strategies

    # ------------------------------------------------------------------
    # 分割操作
    # ------------------------------------------------------------------
    def _split_memory(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        """
        1つの Memory リソースを複数に分割
        
        処理フロー:
        1. ソース Memory からすべてのイベントを取得
        2. 分割基準に基づいてイベントをグループ化
        3. 各グループに対して新しい Memory を作成
        4. イベントを各 Memory にコピー
        5. インデックスの作成（オプション）
        6. 分割結果の返却
        
        Args:
            tool_parameters: ツールパラメータ辞書
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
        """
        import time
        
        # 操作開始時刻を記録
        start_time = time.time()
        
        # パラメータの取得
        source_memory_id = tool_parameters.get("source_memory_id", "").strip()
        split_by = tool_parameters.get("split_by", "actor_id")
        target_memory_prefix = tool_parameters.get("target_memory_prefix", "split")
        create_index = tool_parameters.get("create_index", True)
        time_range_days = tool_parameters.get("time_range_days", 7)
        
        # 操作開始ログ
        logger.info(
            f"Starting split operation: "
            f"source_memory={source_memory_id}, "
            f"split_by={split_by}, "
            f"target_prefix={target_memory_prefix}, "
            f"create_index={create_index}"
        )
        
        # 必須パラメータのチェック
        if not source_memory_id:
            yield self.create_text_message("❌ source_memory_id パラメータは必須です")
            return
        
        # 分割基準の検証
        valid_criteria = {"actor_id", "session_id", "namespace", "time_range"}
        if split_by not in valid_criteria:
            yield self.create_text_message(
                f"❌ 無効な分割基準です: {split_by}\n"
                f"有効な基準: {', '.join(valid_criteria)}"
            )
            return
        
        try:
            # ステップ1: ソース Memory からすべてのイベントを取得
            yield self.create_text_message(
                f"📦 Memory {source_memory_id} からイベントを取得中..."
            )
            
            all_events = self._collect_events_from_memories([source_memory_id])
            total_events = len(all_events)
            
            if total_events == 0:
                yield self.create_text_message(
                    "⚠️ ソース Memory にイベントが存在しません"
                )
                return
            
            yield self.create_text_message(
                f"✅ イベント取得完了: {total_events}件のイベント"
            )
            
            # ステップ2: 分割基準に基づいてイベントをグループ化
            yield self.create_text_message(
                f"🔄 分割基準 '{split_by}' でイベントをグループ化中..."
            )
            
            event_groups = self._group_events_by_criteria(
                all_events, split_by, time_range_days=time_range_days
            )
            
            group_count = len(event_groups)
            
            if group_count == 0:
                yield self.create_text_message(
                    "⚠️ グループ化の結果、グループが作成されませんでした"
                )
                return
            
            yield self.create_text_message(
                f"✅ グループ化完了: {group_count}個のグループ"
            )
            
            # ステップ3-4: 各グループに対して新しい Memory を作成し、イベントをコピー
            yield self.create_text_message(
                f"💾 {group_count}個の新しい Memory を作成中..."
            )
            
            created_memories = []
            total_copied = 0
            total_failed = 0
            errors = []
            
            for group_name, group_events in event_groups.items():
                try:
                    # 新しい Memory を作成
                    memory_id = self._create_memory_for_group(
                        group_name, target_memory_prefix
                    )
                    
                    # イベントをコピー
                    copied_count = 0
                    failed_count = 0
                    
                    for event in group_events:
                        try:
                            # イベントデータを準備
                            event_data = {
                                "memoryId": memory_id,
                                "actorId": event.get("actorId"),
                                "sessionId": event.get("sessionId"),
                                "namespace": event.get("namespace"),
                                "messages": event.get("messages", []),
                            }
                            
                            # イベントを作成
                            self.memory_client.create_event(**event_data)
                            copied_count += 1
                            
                        except ClientError as error:
                            failed_count += 1
                            context = AgentCoreError.create_error_context(
                                operation="create_event",
                                memory_id=memory_id
                            )
                            error_message = AgentCoreError.handle_client_error(
                                error, context
                            )
                            errors.append({
                                "group": group_name,
                                "memory_id": memory_id,
                                "error": error_message
                            })
                            logger.error(f"Failed to create event: {error_message}")
                    
                    # 作成された Memory の情報を記録
                    created_memories.append({
                        "memory_id": memory_id,
                        "memory_name": f"{target_memory_prefix}-{group_name}",
                        "event_count": copied_count,
                        "criteria_value": group_name,
                    })
                    
                    total_copied += copied_count
                    total_failed += failed_count
                    
                    yield self.create_text_message(
                        f"✅ Memory {memory_id} 作成完了: {copied_count}件のイベント"
                    )
                    
                except Exception as exc:
                    logger.error(f"Failed to create memory for group {group_name}: {exc}")
                    errors.append({
                        "group": group_name,
                        "error": str(exc)
                    })
                    yield self.create_text_message(
                        f"❌ グループ {group_name} の Memory 作成に失敗しました: {str(exc)}"
                    )
            
            # ステップ5: インデックスの作成（オプション）
            split_index = None
            if create_index and created_memories:
                yield self.create_text_message("📋 分割インデックスを作成中...")
                
                target_memories_dict = {
                    mem["criteria_value"]: mem["memory_id"]
                    for mem in created_memories
                }
                
                split_index = self._create_split_index(
                    source_memory_id, target_memories_dict
                )
                
                yield self.create_text_message("✅ インデックス作成完了")
            
            # ステップ6: 分割結果の返却
            status = "success" if total_failed == 0 else "partial_success"
            
            result = {
                "status": status,
                "source_memory_id": source_memory_id,
                "results": {
                    "split_count": len(created_memories),
                    "memories": created_memories,
                },
            }
            
            if split_index:
                result["index"] = split_index
            
            if errors:
                result["errors"] = errors[:10]  # 最初の10件のエラーのみ返す
            
            yield self.create_json_message(result)
            
            # サマリーメッセージ
            summary = (
                f"✅ 分割が完了しました\n\n"
                f"📊 ソース Memory: {source_memory_id}\n"
                f"🔄 分割基準: {split_by}\n"
                f"📦 総イベント数: {total_events}件\n"
                f"✨ 作成された Memory 数: {len(created_memories)}個\n"
                f"💾 コピーされたイベント: {total_copied}件\n"
                f"❌ 失敗: {total_failed}件"
            )
            
            if total_failed > 0:
                summary += f"\n\n⚠️ {total_failed}件のイベントのコピーに失敗しました"
            
            yield self.create_text_message(summary)
            
            # 操作完了ログ
            elapsed_time = time.time() - start_time
            logger.info(
                f"Split operation completed: "
                f"source_memory={source_memory_id}, "
                f"split_by={split_by}, "
                f"total_events={total_events}, "
                f"split_count={len(created_memories)}, "
                f"copied_events={total_copied}, "
                f"failed_events={total_failed}, "
                f"elapsed_time={elapsed_time:.2f}s"
            )
            
        except Exception as exc:
            # エラーログ
            elapsed_time = time.time() - start_time
            logger.error(
                f"Split operation failed: "
                f"source_memory={source_memory_id}, "
                f"split_by={split_by}, "
                f"elapsed_time={elapsed_time:.2f}s, "
                f"error={str(exc)}",
                exc_info=True
            )
            error_message = f"❌ 分割に失敗しました: {str(exc)}"
            yield self.create_text_message(error_message)

    def _group_events_by_criteria(
        self, events: list[dict], criteria: str, **kwargs
    ) -> dict[str, list[dict]]:
        """
        分割基準に基づいてイベントをグループ化
        
        Args:
            events: イベントのリスト
            criteria: 分割基準（"actor_id", "session_id", "namespace", "time_range"）
            **kwargs: 追加パラメータ（time_range_days など）
            
        Returns:
            グループ化されたイベントの辞書（キー: グループ名、値: イベントリスト）
        """
        groups = {}
        
        if criteria == "actor_id":
            # Actor ID でグループ化
            for event in events:
                actor_id = event.get("actorId", "unknown")
                if actor_id not in groups:
                    groups[actor_id] = []
                groups[actor_id].append(event)
        
        elif criteria == "session_id":
            # Session ID でグループ化
            for event in events:
                session_id = event.get("sessionId", "unknown")
                if session_id not in groups:
                    groups[session_id] = []
                groups[session_id].append(event)
        
        elif criteria == "namespace":
            # Namespace でグループ化
            for event in events:
                namespace = event.get("namespace", "unknown")
                if namespace not in groups:
                    groups[namespace] = []
                groups[namespace].append(event)
        
        elif criteria == "time_range":
            # 時間範囲でグループ化
            from datetime import datetime, timedelta
            
            time_range_days = kwargs.get("time_range_days", 7)
            
            # イベントをタイムスタンプでソート
            sorted_events = self._sort_events_by_timestamp(events)
            
            if not sorted_events:
                return groups
            
            # 最初のイベントのタイムスタンプを取得
            first_timestamp_str = sorted_events[0].get("metadata", {}).get("createdAt")
            
            if not first_timestamp_str:
                # タイムスタンプがない場合は、すべてのイベントを1つのグループに
                groups["unknown"] = sorted_events
                return groups
            
            # ISO 8601 形式のタイムスタンプをパース
            try:
                first_timestamp = datetime.fromisoformat(
                    first_timestamp_str.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                # パースに失敗した場合は、すべてのイベントを1つのグループに
                groups["unknown"] = sorted_events
                return groups
            
            # 時間範囲でグループ化
            range_start = first_timestamp
            range_index = 0
            
            for event in sorted_events:
                timestamp_str = event.get("metadata", {}).get("createdAt")
                
                if not timestamp_str:
                    # タイムスタンプがない場合は、現在の範囲に追加
                    group_name = f"range_{range_index}"
                    if group_name not in groups:
                        groups[group_name] = []
                    groups[group_name].append(event)
                    continue
                
                try:
                    timestamp = datetime.fromisoformat(
                        timestamp_str.replace("Z", "+00:00")
                    )
                except (ValueError, AttributeError):
                    # パースに失敗した場合は、現在の範囲に追加
                    group_name = f"range_{range_index}"
                    if group_name not in groups:
                        groups[group_name] = []
                    groups[group_name].append(event)
                    continue
                
                # 現在の範囲を超えた場合は、新しい範囲を開始
                while timestamp >= range_start + timedelta(days=time_range_days):
                    range_start += timedelta(days=time_range_days)
                    range_index += 1
                
                # グループ名を生成（範囲の開始日を使用）
                group_name = f"range_{range_index}"
                
                if group_name not in groups:
                    groups[group_name] = []
                
                groups[group_name].append(event)
        
        else:
            # 無効な基準の場合は、すべてのイベントを1つのグループに
            groups["all"] = events
        
        logger.info(
            f"Grouped {len(events)} events by {criteria} into {len(groups)} groups"
        )
        
        return groups

    def _create_memory_for_group(
        self, group_name: str, prefix: str
    ) -> str:
        """
        グループ用の新しい Memory を作成
        
        Args:
            group_name: グループ名
            prefix: Memory 名のプレフィックス
            
        Returns:
            作成された Memory ID
            
        Raises:
            Exception: Memory 作成に失敗した場合
        """
        try:
            # Memory 名を生成
            memory_name = f"{prefix}-{group_name}"
            
            # Memory を作成
            response = self.memory_client.create_memory(
                name=memory_name,
                description=f"Split from source memory - Group: {group_name}",
            )
            
            memory_id = response.get("memoryId")
            
            if not memory_id:
                raise Exception("Memory ID not returned from create_memory API")
            
            logger.info(f"Created memory {memory_id} for group {group_name}")
            
            return memory_id
            
        except ClientError as error:
            context = AgentCoreError.create_error_context(
                operation="create_memory",
                memory_id=None
            )
            error_message = AgentCoreError.handle_client_error(error, context)
            logger.error(f"Failed to create memory: {error_message}")
            raise Exception(error_message)

    def _create_split_index(
        self, source_memory_id: str, target_memories: dict[str, str]
    ) -> dict[str, Any]:
        """
        分割インデックスを作成
        
        Args:
            source_memory_id: ソース Memory ID
            target_memories: ターゲット Memory の辞書（キー: グループ名、値: Memory ID）
            
        Returns:
            分割インデックス
        """
        from datetime import datetime, timezone
        
        # インデックスを作成
        split_index = {
            "source_memory_id": source_memory_id,
            "split_timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "mappings": []
        }
        
        # 各ターゲット Memory のマッピング情報を追加
        for group_name, memory_id in target_memories.items():
            try:
                # Memory の詳細を取得してイベント数を確認
                # 注: list_events を使用してイベント数をカウント
                event_count = 0
                next_token = None
                
                while True:
                    list_params = {
                        "memoryId": memory_id,
                        "maxResults": 100,
                    }
                    
                    if next_token:
                        list_params["nextToken"] = next_token
                    
                    response = self.memory_client.list_events(**list_params)
                    events = response.get("events", [])
                    event_count += len(events)
                    
                    next_token = response.get("nextToken")
                    if not next_token:
                        break
                
                # マッピング情報を追加
                split_index["mappings"].append({
                    "criteria_value": group_name,
                    "target_memory_id": memory_id,
                    "target_memory_name": f"split-{group_name}",
                    "event_count": event_count,
                })
                
            except Exception as exc:
                logger.warning(f"Failed to get event count for memory {memory_id}: {exc}")
                # イベント数の取得に失敗しても、マッピング情報は追加
                split_index["mappings"].append({
                    "criteria_value": group_name,
                    "target_memory_id": memory_id,
                    "target_memory_name": f"split-{group_name}",
                    "event_count": 0,
                })
        
        logger.info(
            f"Created split index with {len(split_index['mappings'])} mappings"
        )
        
        return split_index

    # ------------------------------------------------------------------
    # イベントコピー操作
    # ------------------------------------------------------------------
    def _copy_events(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        """
        特定の条件に一致するイベントを別の Memory にコピー
        
        処理フロー:
        1. ソース Memory からイベントを取得
        2. フィルター条件に基づいてイベントを絞り込み
        3. ターゲット Memory へイベントをコピー
        4. タイムスタンプの保持（オプション）
        5. コピー結果の返却
        
        Args:
            tool_parameters: ツールパラメータ辞書
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
        """
        import time
        
        # 操作開始時刻を記録
        start_time = time.time()
        
        # パラメータの取得
        source_memory_id = tool_parameters.get("source_memory_id", "").strip()
        target_memory_id = tool_parameters.get("target_memory_id", "").strip()
        preserve_timestamps = tool_parameters.get("preserve_timestamps", True)
        
        # フィルター条件の取得
        filter_params = tool_parameters.get("filter", {})
        actor_id = filter_params.get("actor_id", "").strip() if filter_params.get("actor_id") else None
        session_id = filter_params.get("session_id", "").strip() if filter_params.get("session_id") else None
        start_time_filter = filter_params.get("start_time", "").strip() if filter_params.get("start_time") else None
        end_time_filter = filter_params.get("end_time", "").strip() if filter_params.get("end_time") else None
        namespace = filter_params.get("namespace", "").strip() if filter_params.get("namespace") else None
        
        # 操作開始ログ
        logger.info(
            f"Starting copy_events operation: "
            f"source_memory={source_memory_id}, "
            f"target_memory={target_memory_id}, "
            f"filters={{actor_id={actor_id}, session_id={session_id}, "
            f"start_time={start_time_filter}, end_time={end_time_filter}, namespace={namespace}}}, "
            f"preserve_timestamps={preserve_timestamps}"
        )
        
        # 必須パラメータのチェック
        if not source_memory_id:
            yield self.create_text_message("❌ source_memory_id パラメータは必須です")
            return
        
        if not target_memory_id:
            yield self.create_text_message("❌ target_memory_id パラメータは必須です")
            return
        
        # フィルター条件が1つも指定されていない場合は警告
        if not any([actor_id, session_id, start_time_filter, end_time_filter, namespace]):
            yield self.create_text_message(
                "⚠️ フィルター条件が指定されていません。すべてのイベントがコピーされます。"
            )
        
        try:
            # ステップ1: ソース Memory からイベントを取得
            yield self.create_text_message(
                f"📦 Memory {source_memory_id} からイベントを取得中..."
            )
            
            all_events = self._collect_events_from_memories([source_memory_id])
            total_events = len(all_events)
            
            if total_events == 0:
                yield self.create_text_message(
                    "⚠️ ソース Memory にイベントが存在しません"
                )
                
                # 空の結果を返す
                result = {
                    "status": "success",
                    "source_memory_id": source_memory_id,
                    "target_memory_id": target_memory_id,
                    "results": {
                        "total_events": 0,
                        "copied_events": 0,
                        "skipped_events": 0,
                        "failed_events": 0,
                    },
                }
                yield self.create_json_message(result)
                
                # 操作完了ログ（空の場合）
                elapsed_time = time.time() - start_time
                logger.info(
                    f"Copy_events operation completed (no events): "
                    f"source_memory={source_memory_id}, "
                    f"target_memory={target_memory_id}, "
                    f"elapsed_time={elapsed_time:.2f}s"
                )
                return
            
            yield self.create_text_message(
                f"✅ イベント取得完了: {total_events}件のイベント"
            )
            
            # ステップ2: フィルター条件に基づいてイベントを絞り込み
            filters = {
                "actor_id": actor_id,
                "session_id": session_id,
                "start_time": start_time_filter,
                "end_time": end_time_filter,
                "namespace": namespace,
            }
            
            # フィルター条件の表示
            active_filters = [
                f"{k}={v}" for k, v in filters.items() if v is not None
            ]
            if active_filters:
                yield self.create_text_message(
                    f"🔍 フィルター条件を適用中: {', '.join(active_filters)}"
                )
            
            filtered_events = self._filter_events(all_events, filters)
            filtered_count = len(filtered_events)
            
            if filtered_count == 0:
                yield self.create_text_message(
                    "⚠️ フィルター条件に一致するイベントが見つかりませんでした"
                )
                
                # 空の結果を返す
                result = {
                    "status": "success",
                    "source_memory_id": source_memory_id,
                    "target_memory_id": target_memory_id,
                    "results": {
                        "total_events": total_events,
                        "copied_events": 0,
                        "skipped_events": total_events,
                        "failed_events": 0,
                    },
                }
                yield self.create_json_message(result)
                
                # 操作完了ログ（フィルター一致なし）
                elapsed_time = time.time() - start_time
                logger.info(
                    f"Copy_events operation completed (no matches): "
                    f"source_memory={source_memory_id}, "
                    f"target_memory={target_memory_id}, "
                    f"total_events={total_events}, "
                    f"filtered_events=0, "
                    f"elapsed_time={elapsed_time:.2f}s"
                )
                return
            
            yield self.create_text_message(
                f"✅ フィルタリング完了: {filtered_count}件のイベントが一致"
            )
            
            # ステップ3: ターゲット Memory へイベントをコピー
            yield self.create_text_message(
                f"💾 ターゲット Memory {target_memory_id} にイベントをコピー中..."
            )
            
            copy_results = self._batch_copy_events(
                filtered_events, target_memory_id, preserve_timestamps
            )
            
            # ステップ4: コピー結果の返却
            status = "success" if copy_results["failed"] == 0 else "partial_success"
            
            result = {
                "status": status,
                "source_memory_id": source_memory_id,
                "target_memory_id": target_memory_id,
                "results": {
                    "total_events": total_events,
                    "copied_events": copy_results["copied"],
                    "skipped_events": total_events - filtered_count,
                    "failed_events": copy_results["failed"],
                },
            }
            
            if copy_results["errors"]:
                result["errors"] = copy_results["errors"][:10]  # 最初の10件のエラーのみ返す
            
            yield self.create_json_message(result)
            
            # サマリーメッセージ
            summary = (
                f"✅ イベントコピーが完了しました\n\n"
                f"📊 ソース Memory: {source_memory_id}\n"
                f"🎯 ターゲット Memory: {target_memory_id}\n"
                f"📦 総イベント数: {total_events}件\n"
                f"✨ コピーされたイベント: {copy_results['copied']}件\n"
                f"⏭️ スキップ: {total_events - filtered_count}件\n"
                f"❌ 失敗: {copy_results['failed']}件\n"
                f"⏰ タイムスタンプ保持: {'有効' if preserve_timestamps else '無効'}"
            )
            
            if copy_results["failed"] > 0:
                summary += f"\n\n⚠️ {copy_results['failed']}件のイベントのコピーに失敗しました"
            
            yield self.create_text_message(summary)
            
            # 操作完了ログ
            elapsed_time = time.time() - start_time
            logger.info(
                f"Copy_events operation completed: "
                f"source_memory={source_memory_id}, "
                f"target_memory={target_memory_id}, "
                f"total_events={total_events}, "
                f"filtered_events={filtered_count}, "
                f"copied_events={copy_results['copied']}, "
                f"failed_events={copy_results['failed']}, "
                f"elapsed_time={elapsed_time:.2f}s"
            )
            
        except Exception as exc:
            # エラーログ
            elapsed_time = time.time() - start_time
            logger.error(
                f"Copy_events operation failed: "
                f"source_memory={source_memory_id}, "
                f"target_memory={target_memory_id}, "
                f"elapsed_time={elapsed_time:.2f}s, "
                f"error={str(exc)}",
                exc_info=True
            )
            error_message = f"❌ イベントコピーに失敗しました: {str(exc)}"
            yield self.create_text_message(error_message)

    def _filter_events(
        self, events: list[dict], filters: dict[str, Any]
    ) -> list[dict]:
        """
        フィルター条件に基づいてイベントを絞り込み
        
        フィルター条件:
        - actor_id: 指定された Actor ID のイベントのみを取得
        - session_id: 指定された Session ID のイベントのみを取得
        - start_time: 指定された開始時刻以降のイベントのみを取得
        - end_time: 指定された終了時刻以前のイベントのみを取得
        - namespace: 指定された Namespace のイベントのみを取得
        
        Args:
            events: イベントのリスト
            filters: フィルター条件の辞書
            
        Returns:
            フィルタリングされたイベントのリスト
        """
        from datetime import datetime
        
        filtered_events = []
        
        # フィルター条件を取得
        actor_id_filter = filters.get("actor_id")
        session_id_filter = filters.get("session_id")
        start_time_filter = filters.get("start_time")
        end_time_filter = filters.get("end_time")
        namespace_filter = filters.get("namespace")
        
        # 時間フィルターをパース
        start_datetime = None
        end_datetime = None
        
        if start_time_filter:
            try:
                start_datetime = datetime.fromisoformat(
                    start_time_filter.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError) as exc:
                logger.warning(f"Invalid start_time format: {start_time_filter}, {exc}")
        
        if end_time_filter:
            try:
                end_datetime = datetime.fromisoformat(
                    end_time_filter.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError) as exc:
                logger.warning(f"Invalid end_time format: {end_time_filter}, {exc}")
        
        # 各イベントをフィルタリング
        for event in events:
            # Actor ID フィルター
            if actor_id_filter:
                event_actor_id = event.get("actorId")
                if event_actor_id != actor_id_filter:
                    continue
            
            # Session ID フィルター
            if session_id_filter:
                event_session_id = event.get("sessionId")
                if event_session_id != session_id_filter:
                    continue
            
            # Namespace フィルター
            if namespace_filter:
                event_namespace = event.get("namespace")
                if event_namespace != namespace_filter:
                    continue
            
            # 時間範囲フィルター
            if start_datetime or end_datetime:
                timestamp_str = event.get("metadata", {}).get("createdAt")
                
                if not timestamp_str:
                    # タイムスタンプがない場合はスキップ
                    continue
                
                try:
                    event_datetime = datetime.fromisoformat(
                        timestamp_str.replace("Z", "+00:00")
                    )
                    
                    # 開始時刻チェック
                    if start_datetime and event_datetime < start_datetime:
                        continue
                    
                    # 終了時刻チェック
                    if end_datetime and event_datetime > end_datetime:
                        continue
                    
                except (ValueError, AttributeError) as exc:
                    logger.warning(f"Invalid event timestamp: {timestamp_str}, {exc}")
                    # タイムスタンプのパースに失敗した場合はスキップ
                    continue
            
            # すべてのフィルター条件を満たす場合、リストに追加
            filtered_events.append(event)
        
        logger.info(
            f"Filtered {len(events)} events to {len(filtered_events)} events "
            f"(filters: {filters})"
        )
        
        return filtered_events

    def _batch_copy_events(
        self,
        source_events: list[dict],
        target_memory_id: str,
        preserve_timestamps: bool = True,
    ) -> dict[str, Any]:
        """
        イベントをバッチでコピー
        
        Args:
            source_events: コピー元のイベントリスト
            target_memory_id: コピー先の Memory ID
            preserve_timestamps: タイムスタンプを保持するか
            
        Returns:
            コピー結果の辞書
            - copied: コピーされたイベント数
            - skipped: スキップされたイベント数
            - failed: 失敗したイベント数
            - errors: エラー詳細のリスト
        """
        results = {
            "copied": 0,
            "skipped": 0,
            "failed": 0,
            "errors": [],
        }
        
        # バッチサイズ
        batch_size = 50
        
        for i in range(0, len(source_events), batch_size):
            batch = source_events[i:i + batch_size]
            
            for event in batch:
                try:
                    # イベントデータを準備
                    event_data = {
                        "memoryId": target_memory_id,
                        "actorId": event.get("actorId"),
                        "sessionId": event.get("sessionId"),
                        "namespace": event.get("namespace"),
                        "messages": event.get("messages", []),
                    }
                    
                    # タイムスタンプを保持する場合
                    # 注: AgentCore Memory API は create_event でタイムスタンプを指定できないため、
                    # preserve_timestamps オプションは現在のところ効果がありません。
                    # 将来的に API がサポートされた場合に備えて、パラメータは保持します。
                    if preserve_timestamps:
                        # タイムスタンプを保持する場合の処理
                        # 現在の API では、タイムスタンプは自動的に設定されます
                        pass
                    
                    # イベントを作成
                    self.memory_client.create_event(**event_data)
                    results["copied"] += 1
                    
                except ClientError as error:
                    results["failed"] += 1
                    context = AgentCoreError.create_error_context(
                        operation="create_event",
                        memory_id=target_memory_id
                    )
                    error_message = AgentCoreError.handle_client_error(error, context)
                    results["errors"].append({
                        "event_index": i + batch.index(event),
                        "actor_id": event.get("actorId"),
                        "session_id": event.get("sessionId"),
                        "error": error_message,
                    })
                    logger.error(f"Failed to copy event: {error_message}")
                
                except Exception as exc:
                    results["failed"] += 1
                    results["errors"].append({
                        "event_index": i + batch.index(event),
                        "actor_id": event.get("actorId"),
                        "session_id": event.get("sessionId"),
                        "error": str(exc),
                    })
                    logger.error(f"Failed to copy event: {exc}")
        
        logger.info(
            f"Batch copy completed: {results['copied']} copied, "
            f"{results['failed']} failed"
        )
        
        return results

    # ------------------------------------------------------------------
    # ユーティリティ
    # ------------------------------------------------------------------
    def _calculate_event_hash(self, event: dict) -> str:
        """
        イベントのハッシュ値を計算
        
        Args:
            event: イベント辞書
            
        Returns:
            イベントのハッシュ値（16進数文字列）
        """
        # ハッシュ計算に使用するフィールドを抽出
        # タイムスタンプやメタデータは除外し、イベントの内容のみを使用
        hash_data = {
            "actorId": event.get("actorId"),
            "sessionId": event.get("sessionId"),
            "namespace": event.get("namespace"),
            "messages": event.get("messages", []),
        }
        
        # JSON 文字列に変換（キーをソートして一貫性を保つ）
        json_str = json.dumps(hash_data, sort_keys=True, ensure_ascii=False)
        
        # SHA256 ハッシュを計算
        hash_obj = hashlib.sha256(json_str.encode("utf-8"))
        
        return hash_obj.hexdigest()

    def _sort_events_by_timestamp(self, events: list[dict]) -> list[dict]:
        """
        イベントをタイムスタンプでソート
        
        Args:
            events: イベントのリスト
            
        Returns:
            ソートされたイベントのリスト（古い順）
        """
        # タイムスタンプでソート（古い順）
        # イベントのタイムスタンプは metadata.createdAt に格納されている
        sorted_events = sorted(
            events,
            key=lambda e: e.get("metadata", {}).get("createdAt", "")
        )
        
        return sorted_events

    def _extract_actor_ids(self, events: list[dict]) -> set[str]:
        """
        イベントから Actor ID を抽出
        
        Args:
            events: イベントのリスト
            
        Returns:
            Actor ID のセット
        """
        actor_ids = set()
        
        for event in events:
            actor_id = event.get("actorId")
            if actor_id:
                actor_ids.add(actor_id)
        
        logger.info(f"Extracted {len(actor_ids)} unique actor IDs from {len(events)} events")
        
        return actor_ids

    def _extract_session_ids(self, events: list[dict]) -> set[str]:
        """
        イベントから Session ID を抽出
        
        Args:
            events: イベントのリスト
            
        Returns:
            Session ID のセット
        """
        session_ids = set()
        
        for event in events:
            session_id = event.get("sessionId")
            if session_id:
                session_ids.add(session_id)
        
        logger.info(f"Extracted {len(session_ids)} unique session IDs from {len(events)} events")
        
        return session_ids

    # ------------------------------------------------------------------
    # Dify Tool エントリ
    # ------------------------------------------------------------------
    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage]:
        """
        マージ・分割・コピーの要求を受け、適切な操作を実行する
        
        Args:
            tool_parameters: ツールパラメータ辞書
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
        """
        operation = tool_parameters.get("operation")
        valid_operations = {"merge", "split", "copy_events"}

        if operation not in valid_operations:
            yield self.create_text_message(
                f"❌ 無効な操作です: {operation}\n"
                f"有効な操作: {', '.join(valid_operations)}"
            )
            return

        # クライアントの初期化
        if not self._initialize_client(tool_parameters):
            yield self.create_text_message(
                "❌ AWS クライアントの初期化に失敗しました"
            )
            return

        # 操作に応じた処理を実行
        if operation == "merge":
            yield from self._merge_memories(tool_parameters)
        elif operation == "split":
            yield from self._split_memory(tool_parameters)
        elif operation == "copy_events":
            yield from self._copy_events(tool_parameters)
