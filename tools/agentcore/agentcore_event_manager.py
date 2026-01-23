"""
場所: tools/agentcore/agentcore_event_manager.py
内容: Bedrock AgentCore Memory イベントの検索、削除、エクスポートを行う Dify ツール。
目的: Memory に記録されたイベントを管理し、会話履歴の分析とデバッグを効率的に行えるようにする。
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
from botocore.exceptions import ClientError

# AgentCore SDK は追加依存のため、同梱されていない場合も考慮する
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from bedrock_agentcore.memory import MemoryClient

    AGENTCORE_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - SDK 未導入環境に備える
    MemoryClient = None
    AGENTCORE_SDK_AVAILABLE = False

logger = logging.getLogger(__name__)


class AgentCoreEventManagerTool(Tool):
    """Memory イベントの検索、削除、エクスポートを行うツール本体."""

    memory_client: Any = None

    # ------------------------------------------------------------------
    # 初期化
    # ------------------------------------------------------------------
    def _initialize_memory_client(self, tool_parameters: dict[str, Any]) -> bool:
        """
        AWS 資格情報から MemoryClient を構築する
        
        Args:
            tool_parameters: ツールパラメータ（AWS認証情報を含む）
            
        Returns:
            初期化が成功した場合は True、失敗した場合は False
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
            logger.info("AgentCore Memory client initialized successfully")
            return True
        except Exception as exc:  # pragma: no cover - SDK 例外
            logger.error(f"Failed to initialize Memory client: {exc}", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # Dify Tool エントリ
    # ------------------------------------------------------------------
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        """
        イベント管理操作のメインエントリポイント
        
        Args:
            tool_parameters: ツールパラメータ
            
        Yields:
            ToolInvokeMessage: 操作結果のメッセージ
        """
        operation = tool_parameters.get("operation")
        valid_operations = {"list", "delete", "delete_batch", "export"}
        
        if operation not in valid_operations:
            yield self.create_text_message(
                f"❌ 無効な操作です: '{operation}'\n"
                f"有効な操作: {', '.join(valid_operations)}"
            )
            return

        # MemoryClient を初期化
        if not self.memory_client and not self._initialize_memory_client(tool_parameters):
            yield self.create_text_message("❌ AgentCore Memory クライアントの初期化に失敗しました")
            return

        # 操作に応じて適切なメソッドを呼び出し
        if operation == "list":
            yield from self._list_events(tool_parameters)
        elif operation == "delete":
            yield from self._delete_event(tool_parameters)
        elif operation == "delete_batch":
            yield from self._delete_events_batch(tool_parameters)
        elif operation == "export":
            yield from self._export_events(tool_parameters)

    # ------------------------------------------------------------------
    # イベント操作メソッド（後続のタスクで実装）
    # ------------------------------------------------------------------
    def _list_events(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        イベント一覧を取得する
        
        Args:
            tool_parameters: ツールパラメータ
                - memory_id: Memory ID（必須）
                - actor_id: Actor ID（オプション）
                - session_id: Session ID（オプション）
                - start_time: 開始時刻（オプション、ISO 8601形式）
                - end_time: 終了時刻（オプション、ISO 8601形式）
                - max_results: 最大取得件数（オプション、デフォルト100、最大1000）
                
        Yields:
            ToolInvokeMessage: イベント一覧の結果
        """
        # 必須パラメータの検証
        memory_id = tool_parameters.get("memory_id", "").strip()
        if not memory_id:
            yield self.create_text_message("❌ Memory ID は必須です")
            return
        
        # オプションパラメータの取得
        actor_id = tool_parameters.get("actor_id", "").strip() or None
        session_id = tool_parameters.get("session_id", "").strip() or None
        start_time = tool_parameters.get("start_time", "").strip() or None
        end_time = tool_parameters.get("end_time", "").strip() or None
        
        # 件数制限の処理（デフォルト100、最大1000）
        max_results = tool_parameters.get("max_results")
        if max_results is None:
            max_results = 100  # デフォルト制限（要件 7.1）
        else:
            try:
                max_results = int(max_results)
                if max_results < 1:
                    max_results = 100
                elif max_results > 1000:
                    max_results = 1000  # 最大制限（要件 7.2）
            except (ValueError, TypeError):
                max_results = 100
        
        # エラーコンテキストの作成
        context = AgentCoreError.create_error_context(
            operation="list_events",
            memory_id=memory_id,
            actor_id=actor_id,
            session_id=session_id
        )
        
        # フィルター条件の表示
        filter_info = [f"Memory ID: {memory_id}"]
        if actor_id:
            filter_info.append(f"Actor ID: {actor_id}")
        if session_id:
            filter_info.append(f"Session ID: {session_id}")
        if start_time:
            filter_info.append(f"開始時刻: {start_time}")
        if end_time:
            filter_info.append(f"終了時刻: {end_time}")
        filter_info.append(f"最大件数: {max_results}")
        
        yield self.create_text_message(
            f"📋 イベント一覧を取得しています...\n\n"
            f"フィルター条件:\n" + "\n".join(f"  • {info}" for info in filter_info)
        )
        
        try:
            # list_events メソッドを呼び出し
            # MemoryClient の list_events メソッドのパラメータに合わせて呼び出し
            list_params = {
                "memory_id": memory_id,
                "max_results": max_results
            }
            
            # オプションパラメータを追加
            if actor_id:
                list_params["actor_id"] = actor_id
            if session_id:
                list_params["session_id"] = session_id
            if start_time:
                list_params["start_time"] = start_time
            if end_time:
                list_params["end_time"] = end_time
            
            # イベント一覧を取得（ページネーション対応）
            events = self._list_events_with_pagination(**list_params)
            
            # イベントを整形
            formatted_events = []
            for event in events:
                metadata = event.get("metadata", {}) or {}
                formatted_event = {
                    "event_id": event.get("eventId"),
                    "actor_id": event.get("actorId"),
                    "session_id": event.get("sessionId"),
                    "event_timestamp": event.get("eventTimestamp"),
                    "messages": event.get("messages", []),
                    "created_at": metadata.get("createdAt"),
                    "branch": event.get("branch")
                }
                formatted_events.append(formatted_event)
            
            # 結果を返す
            response_data = {
                "message": f"イベント一覧を取得しました（{len(formatted_events)}件）",
                "data": {
                    "memory_id": memory_id,
                    "actor_id": actor_id,
                    "session_id": session_id,
                    "start_time": start_time,
                    "end_time": end_time,
                    "max_results": max_results,
                    "events_count": len(formatted_events),
                    "events": formatted_events
                }
            }
            
            yield self.create_json_message(response_data)
            
            # サマリーメッセージ
            yield self.create_text_message(
                f"✅ イベント一覧の取得が完了しました\n\n"
                f"取得件数: {len(formatted_events)}件"
            )
            
        except ClientError as e:
            error_message = AgentCoreError.handle_client_error(e, context)
            logger.error(f"Failed to list events: {error_message}", exc_info=True)
            yield self.create_text_message(f"❌ {error_message}")
        except Exception as exc:
            logger.error(f"Unexpected error in list_events: {exc}", exc_info=True)
            yield self.create_text_message(
                f"❌ 予期しないエラーが発生しました: {exc}\n"
                f"操作: イベント一覧取得\n"
                f"Memory ID: {memory_id}"
            )
    
    def _list_events_with_pagination(
        self,
        memory_id: str,
        max_results: int,
        actor_id: Optional[str] = None,
        session_id: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        ページネーションをサポートするイベント一覧取得
        
        Args:
            memory_id: Memory ID
            max_results: 最大取得件数
            actor_id: Actor ID（オプション）
            session_id: Session ID（オプション）
            start_time: 開始時刻（オプション）
            end_time: 終了時刻（オプション）
            
        Returns:
            イベントのリスト
        """
        all_events = []
        next_token = None
        
        while len(all_events) < max_results:
            # リクエストパラメータを構築
            request_params = {
                "memory_id": memory_id,
                "max_results": min(100, max_results - len(all_events))
            }
            
            if actor_id:
                request_params["actor_id"] = actor_id
            if session_id:
                request_params["session_id"] = session_id
            if start_time:
                request_params["start_time"] = start_time
            if end_time:
                request_params["end_time"] = end_time
            if next_token:
                request_params["next_token"] = next_token
            
            # list_events を呼び出し
            response = self.memory_client.list_events(**request_params)
            
            # レスポンスからイベントを取得
            if isinstance(response, dict):
                events = response.get("events", [])
                next_token = response.get("nextToken")
            elif isinstance(response, list):
                # レスポンスがリストの場合（SDK のバージョンによる）
                events = response
                next_token = None
            else:
                events = []
                next_token = None
            
            all_events.extend(events)
            
            # 次のページがない場合は終了
            if not next_token:
                break
        
        # 最大件数まで切り詰め
        return all_events[:max_results]

    def _delete_event(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        イベントを削除する
        
        Args:
            tool_parameters: ツールパラメータ
                - memory_id: Memory ID（必須）
                - event_id: イベント ID（必須）
                
        Yields:
            ToolInvokeMessage: 削除結果のメッセージ
        """
        # 必須パラメータの検証
        memory_id = tool_parameters.get("memory_id", "").strip()
        event_id = tool_parameters.get("event_id", "").strip()
        
        if not memory_id:
            yield self.create_text_message("❌ Memory ID は必須です")
            return
        
        if not event_id:
            yield self.create_text_message("❌ イベント ID は必須です")
            return
        
        # エラーコンテキストの作成
        context = AgentCoreError.create_error_context(
            operation="delete_event",
            memory_id=memory_id,
            event_id=event_id
        )
        
        yield self.create_text_message(
            f"🗑️ イベントを削除しています...\n\n"
            f"Memory ID: {memory_id}\n"
            f"イベント ID: {event_id}"
        )
        
        try:
            # gmdp_client を使用してイベントを削除
            self.memory_client.gmdp_client.delete_event(
                memoryId=memory_id,
                eventId=event_id
            )
            
            # 削除確認メッセージを返す
            yield self.create_text_message(
                f"✅ イベントを削除しました\n\n"
                f"Memory ID: {memory_id}\n"
                f"イベント ID: {event_id}"
            )
            
            # JSON レスポンスも返す
            yield self.create_json_message({
                "message": "イベントを削除しました",
                "data": {
                    "memory_id": memory_id,
                    "event_id": event_id,
                    "status": "deleted"
                }
            })
            
        except ClientError as e:
            error_message = AgentCoreError.handle_client_error(e, context)
            logger.error(f"Failed to delete event: {error_message}", exc_info=True)
            yield self.create_text_message(f"❌ {error_message}")
        except Exception as exc:
            logger.error(f"Unexpected error in delete_event: {exc}", exc_info=True)
            yield self.create_text_message(
                f"❌ 予期しないエラーが発生しました: {exc}\n"
                f"操作: イベント削除\n"
                f"Memory ID: {memory_id}\n"
                f"イベント ID: {event_id}"
            )

    def _delete_events_batch(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        イベントをバッチ削除する
        
        Args:
            tool_parameters: ツールパラメータ
                - memory_id: Memory ID（必須）
                - event_ids: イベント ID のリスト（必須、最大100件）
                
        Yields:
            ToolInvokeMessage: バッチ削除結果のメッセージ
        """
        # 必須パラメータの検証
        memory_id = tool_parameters.get("memory_id", "").strip()
        event_ids_param = tool_parameters.get("event_ids", "")
        
        if not memory_id:
            yield self.create_text_message("❌ Memory ID は必須です")
            return
        
        # event_ids をパース（カンマ区切り文字列またはリスト）
        if isinstance(event_ids_param, str):
            # カンマ区切り文字列をリストに変換
            event_ids = [eid.strip() for eid in event_ids_param.split(",") if eid.strip()]
        elif isinstance(event_ids_param, list):
            # すでにリストの場合はそのまま使用
            event_ids = [str(eid).strip() for eid in event_ids_param if str(eid).strip()]
        else:
            event_ids = []
        
        if not event_ids:
            yield self.create_text_message("❌ イベント ID のリストは必須です")
            return
        
        # 最大バッチサイズ（100件）を適用（要件 7.3）
        batch_size = 100
        if len(event_ids) > batch_size:
            yield self.create_text_message(
                f"⚠️ イベント ID が {len(event_ids)} 件指定されていますが、"
                f"最大バッチサイズは {batch_size} 件です。最初の {batch_size} 件のみ削除します。"
            )
            event_ids = event_ids[:batch_size]
        
        # エラーコンテキストの作成
        context = AgentCoreError.create_error_context(
            operation="delete_events_batch",
            memory_id=memory_id
        )
        
        yield self.create_text_message(
            f"🗑️ イベントをバッチ削除しています...\n\n"
            f"Memory ID: {memory_id}\n"
            f"削除対象件数: {len(event_ids)}件"
        )
        
        # バッチ削除を実行
        deleted_count = 0
        failed_count = 0
        failed_events = []
        
        try:
            # ループ処理で各イベントを削除
            for i, event_id in enumerate(event_ids, 1):
                try:
                    # gmdp_client を使用してイベントを削除
                    self.memory_client.gmdp_client.delete_event(
                        memoryId=memory_id,
                        eventId=event_id
                    )
                    deleted_count += 1
                    logger.info(f"Deleted event {i}/{len(event_ids)}: {event_id}")
                    
                except ClientError as e:
                    failed_count += 1
                    error_code = e.response.get('Error', {}).get('Code', 'UnknownError')
                    failed_events.append({
                        "event_id": event_id,
                        "error_code": error_code,
                        "error_message": str(e)
                    })
                    logger.error(f"Failed to delete event {event_id}: {e}")
                    
                except Exception as e:
                    failed_count += 1
                    failed_events.append({
                        "event_id": event_id,
                        "error_code": "UnexpectedError",
                        "error_message": str(e)
                    })
                    logger.error(f"Unexpected error deleting event {event_id}: {e}")
            
            # 結果を返す
            result_message = f"✅ イベントバッチ削除が完了しました\n\n"
            result_message += f"削除成功: {deleted_count}件\n"
            result_message += f"削除失敗: {failed_count}件\n"
            result_message += f"合計: {len(event_ids)}件"
            
            if failed_count > 0:
                result_message += f"\n\n⚠️ {failed_count}件のイベント削除に失敗しました"
            
            yield self.create_text_message(result_message)
            
            # JSON レスポンスも返す
            response_data = {
                "message": "イベントバッチ削除が完了しました",
                "data": {
                    "memory_id": memory_id,
                    "total_count": len(event_ids),
                    "deleted_count": deleted_count,
                    "failed_count": failed_count,
                    "status": "completed" if failed_count == 0 else "partial_success"
                }
            }
            
            # 失敗したイベントの情報を含める
            if failed_events:
                response_data["data"]["failed_events"] = failed_events
            
            yield self.create_json_message(response_data)
            
        except Exception as exc:
            logger.error(f"Unexpected error in delete_events_batch: {exc}", exc_info=True)
            yield self.create_text_message(
                f"❌ 予期しないエラーが発生しました: {exc}\n"
                f"操作: イベントバッチ削除\n"
                f"Memory ID: {memory_id}\n"
                f"削除成功: {deleted_count}件\n"
                f"削除失敗: {failed_count}件"
            )

    def _export_events(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        イベントをエクスポートする
        
        Args:
            tool_parameters: ツールパラメータ
                - memory_id: Memory ID（必須）
                - actor_id: Actor ID（オプション）
                - session_id: Session ID（オプション）
                - start_time: 開始時刻（オプション、ISO 8601形式）
                - end_time: 終了時刻（オプション、ISO 8601形式）
                - format: エクスポート形式（json または csv、デフォルトは json）
                - max_results: 最大取得件数（オプション、デフォルト100、最大1000）
                
        Yields:
            ToolInvokeMessage: エクスポートされたデータ
        """
        # 必須パラメータの検証
        memory_id = tool_parameters.get("memory_id", "").strip()
        if not memory_id:
            yield self.create_text_message("❌ Memory ID は必須です")
            return
        
        # オプションパラメータの取得
        actor_id = tool_parameters.get("actor_id", "").strip() or None
        session_id = tool_parameters.get("session_id", "").strip() or None
        start_time = tool_parameters.get("start_time", "").strip() or None
        end_time = tool_parameters.get("end_time", "").strip() or None
        export_format = tool_parameters.get("format", "json").strip().lower()
        
        # エクスポート形式の検証
        if export_format not in {"json", "csv"}:
            yield self.create_text_message(
                f"❌ 無効なエクスポート形式です: '{export_format}'\n"
                f"有効な形式: json, csv"
            )
            return
        
        # 件数制限の処理（デフォルト100、最大1000）
        max_results = tool_parameters.get("max_results")
        if max_results is None:
            max_results = 100
        else:
            try:
                max_results = int(max_results)
                if max_results < 1:
                    max_results = 100
                elif max_results > 1000:
                    max_results = 1000
            except (ValueError, TypeError):
                max_results = 100
        
        # エラーコンテキストの作成
        context = AgentCoreError.create_error_context(
            operation="export_events",
            memory_id=memory_id,
            actor_id=actor_id,
            session_id=session_id
        )
        
        # フィルター条件の表示
        filter_info = [f"Memory ID: {memory_id}"]
        if actor_id:
            filter_info.append(f"Actor ID: {actor_id}")
        if session_id:
            filter_info.append(f"Session ID: {session_id}")
        if start_time:
            filter_info.append(f"開始時刻: {start_time}")
        if end_time:
            filter_info.append(f"終了時刻: {end_time}")
        filter_info.append(f"形式: {export_format.upper()}")
        filter_info.append(f"最大件数: {max_results}")
        
        yield self.create_text_message(
            f"📤 イベントをエクスポートしています...\n\n"
            f"エクスポート条件:\n" + "\n".join(f"  • {info}" for info in filter_info)
        )
        
        try:
            # イベント一覧を取得
            list_params = {
                "memory_id": memory_id,
                "max_results": max_results
            }
            
            if actor_id:
                list_params["actor_id"] = actor_id
            if session_id:
                list_params["session_id"] = session_id
            if start_time:
                list_params["start_time"] = start_time
            if end_time:
                list_params["end_time"] = end_time
            
            # イベント一覧を取得（ページネーション対応）
            events = self._list_events_with_pagination(**list_params)
            
            if not events:
                yield self.create_text_message(
                    "⚠️ エクスポートするイベントが見つかりませんでした"
                )
                return
            
            # 形式に応じてエクスポート
            if export_format == "json":
                exported_data = self._export_events_as_json(events, memory_id, actor_id, session_id)
                yield self.create_json_message(exported_data)
                
                yield self.create_text_message(
                    f"✅ イベントを JSON 形式でエクスポートしました\n\n"
                    f"エクスポート件数: {len(events)}件"
                )
                
            elif export_format == "csv":
                csv_data = self._export_events_as_csv(events)
                
                # CSV データをテキストメッセージとして返す
                yield self.create_text_message(
                    f"✅ イベントを CSV 形式でエクスポートしました\n\n"
                    f"エクスポート件数: {len(events)}件\n\n"
                    f"--- CSV データ ---\n{csv_data}"
                )
                
                # JSON メッセージとしても返す（CSV データを含む）
                yield self.create_json_message({
                    "message": "イベントを CSV 形式でエクスポートしました",
                    "data": {
                        "memory_id": memory_id,
                        "actor_id": actor_id,
                        "session_id": session_id,
                        "format": "csv",
                        "events_count": len(events),
                        "csv_data": csv_data
                    }
                })
            
        except ClientError as e:
            error_message = AgentCoreError.handle_client_error(e, context)
            logger.error(f"Failed to export events: {error_message}", exc_info=True)
            yield self.create_text_message(f"❌ {error_message}")
        except Exception as exc:
            logger.error(f"Unexpected error in export_events: {exc}", exc_info=True)
            yield self.create_text_message(
                f"❌ 予期しないエラーが発生しました: {exc}\n"
                f"操作: イベントエクスポート\n"
                f"Memory ID: {memory_id}"
            )
    
    def _export_events_as_json(
        self,
        events: List[Dict[str, Any]],
        memory_id: str,
        actor_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        イベントを JSON 形式でエクスポートする
        
        Args:
            events: イベントのリスト
            memory_id: Memory ID
            actor_id: Actor ID（オプション）
            session_id: Session ID（オプション）
            
        Returns:
            JSON 形式のエクスポートデータ
        """
        # イベントを整形
        formatted_events = []
        for event in events:
            metadata = event.get("metadata", {}) or {}
            formatted_event = {
                "event_id": event.get("eventId"),
                "actor_id": event.get("actorId"),
                "session_id": event.get("sessionId"),
                "event_timestamp": event.get("eventTimestamp"),
                "messages": event.get("messages", []),
                "created_at": metadata.get("createdAt"),
                "branch": event.get("branch")
            }
            formatted_events.append(formatted_event)
        
        # JSON エクスポートデータを構築
        export_data = {
            "message": f"イベントを JSON 形式でエクスポートしました（{len(formatted_events)}件）",
            "data": {
                "memory_id": memory_id,
                "actor_id": actor_id,
                "session_id": session_id,
                "format": "json",
                "events_count": len(formatted_events),
                "events": formatted_events
            }
        }
        
        return export_data
    
    def _export_events_as_csv(self, events: List[Dict[str, Any]]) -> str:
        """
        イベントを CSV 形式でエクスポートする
        
        Args:
            events: イベントのリスト
            
        Returns:
            CSV 形式の文字列
        """
        import csv
        from io import StringIO
        
        # CSV ヘッダー
        csv_headers = [
            "event_id",
            "actor_id",
            "session_id",
            "event_timestamp",
            "created_at",
            "message_count",
            "messages_json",
            "branch_json"
        ]
        
        # CSV データを構築
        csv_buffer = StringIO()
        csv_writer = csv.DictWriter(csv_buffer, fieldnames=csv_headers)
        csv_writer.writeheader()
        
        for event in events:
            metadata = event.get("metadata", {}) or {}
            messages = event.get("messages", [])
            branch = event.get("branch")
            
            # メッセージを JSON 文字列に変換
            messages_json = json.dumps(messages, ensure_ascii=False) if messages else ""
            branch_json = json.dumps(branch, ensure_ascii=False) if branch else ""
            
            csv_row = {
                "event_id": event.get("eventId", ""),
                "actor_id": event.get("actorId", ""),
                "session_id": event.get("sessionId", ""),
                "event_timestamp": event.get("eventTimestamp", ""),
                "created_at": metadata.get("createdAt", ""),
                "message_count": len(messages),
                "messages_json": messages_json,
                "branch_json": branch_json
            }
            
            csv_writer.writerow(csv_row)
        
        # CSV データを取得
        csv_data = csv_buffer.getvalue()
        csv_buffer.close()
        
        return csv_data
