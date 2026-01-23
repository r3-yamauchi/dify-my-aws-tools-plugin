"""
場所: tools/agentcore/agentcore_memory_backup.py
内容: Bedrock AgentCore Memory のバックアップ・リストア機能を提供する Dify ツール。
目的: Memory リソースの完全なバックアップと復元機能を提供し、ディザスタリカバリと環境間移行を実現する。
"""

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
except ImportError:  # pragma: no cover - SDK 未導入環境に備える
    MemoryClient = None
    AGENTCORE_SDK_AVAILABLE = False

try:
    from utils.utils import (
        build_boto3_client_kwargs,
        resolve_aws_credentials,
        reset_clients_on_credential_change,
    )
    from utils.error_handler import AgentCoreError
    from utils.backup_utils import BackupUtils
except ModuleNotFoundError:  # pragma: no cover
    from utils.utils import (
        build_boto3_client_kwargs,
        resolve_aws_credentials,
        reset_clients_on_credential_change,
    )
    from utils.error_handler import AgentCoreError
    from utils.backup_utils import BackupUtils

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class AgentCoreMemoryBackupTool(Tool):
    """AgentCore Memory のバックアップ・リストア・検証機能を提供するツール"""

    memory_client: Any = None
    s3_client: Any = None
    _client_credentials_signature: Optional[tuple] = None

    # ------------------------------------------------------------------
    # 初期化
    # ------------------------------------------------------------------
    def _initialize_clients(self, tool_parameters: dict[str, Any]) -> bool:
        """
        AWS 認証情報から MemoryClient と S3 Client を初期化する
        
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
            # 標準的な認証情報解決パターンを使用
            credentials = resolve_aws_credentials(self, tool_parameters)
            aws_region = credentials.get("aws_region") or "us-east-1"

            # 認証情報が変更された場合、クライアントをリセット
            reset_clients_on_credential_change(
                self, credentials, ["memory_client", "s3_client"]
            )

            # Memory Client の初期化
            if not self.memory_client:
                # MemoryClient は内部で boto3 を使用するため、
                # boto3 の標準認証チェーン（環境変数、~/.aws/credentials、IAMロールなど）が自動的に使用される
                self.memory_client = MemoryClient(region_name=aws_region)
                logger.info("AgentCore Memory client initialized")

            # S3 Client の初期化
            if not self.s3_client:
                client_kwargs = build_boto3_client_kwargs(credentials)
                self.s3_client = boto3.client("s3", **client_kwargs)
                logger.info("S3 client initialized")

            return True

        except Exception as exc:  # pragma: no cover - SDK 例外
            logger.error(f"Failed to initialize clients: {exc}")
            return False

    def _collect_memory_data(self, memory_id: str) -> dict[str, Any]:
        """
        Memory リソースからすべてのデータを収集
        
        イベント、戦略設定、メタデータを取得します。
        
        Args:
            memory_id: Memory ID
            
        Returns:
            収集されたデータ（イベント、戦略、メタデータを含む辞書）
            
        Raises:
            Exception: データ収集に失敗した場合
        """
        try:
            # Memory の詳細情報を取得
            memory_details = self.memory_client.get_memory(memory_id=memory_id)
            
            # イベントをページネーションで取得
            all_events = []
            next_token = None
            
            logger.info(f"Collecting events from memory {memory_id}")
            
            while True:
                # list_events API を呼び出し
                list_params = {
                    "memory_id": memory_id,
                    "max_results": 100,  # 1回のリクエストで最大100件
                }
                if next_token:
                    list_params["next_token"] = next_token
                
                response = self.memory_client.list_events(**list_params)
                
                # イベントを追加
                events = response.get("events", [])
                all_events.extend(events)
                
                # 次のページがあるかチェック
                next_token = response.get("nextToken")
                if not next_token:
                    break
                
                logger.info(f"Collected {len(all_events)} events so far...")
            
            logger.info(f"Total events collected: {len(all_events)}")
            
            # 収集したデータを構造化
            collected_data = {
                "memory_id": memory_id,
                "memory_name": memory_details.get("name", ""),
                "description": memory_details.get("description", ""),
                "strategies": memory_details.get("strategies", []),
                "tags": memory_details.get("tags", {}),
                "events": all_events,
            }
            
            return collected_data
            
        except ClientError as error:
            context = AgentCoreError.create_error_context(
                operation="collect_memory_data",
                memory_id=memory_id
            )
            error_message = AgentCoreError.handle_client_error(error, context)
            raise Exception(error_message)
        except Exception as exc:
            logger.error(f"Failed to collect memory data: {exc}")
            raise

    def _create_backup_metadata(
        self, memory_id: str, event_count: int, file_size: int = 0, compressed: bool = False, encrypted: bool = False
    ) -> dict[str, Any]:
        """
        バックアップメタデータを作成
        
        Args:
            memory_id: Memory ID
            event_count: イベント数
            file_size: ファイルサイズ（バイト）
            compressed: 圧縮されているか
            encrypted: 暗号化されているか
            
        Returns:
            バックアップメタデータ辞書
        """
        from datetime import datetime, timezone
        
        metadata = {
            "memory_id": memory_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "event_count": event_count,
            "file_size": file_size,
            "compressed": compressed,
            "encrypted": encrypted,
        }
        
        return metadata

    # ------------------------------------------------------------------
    # バックアップ操作
    # ------------------------------------------------------------------
    def _backup_memory(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        """
        Memory リソースの完全バックアップを作成
        
        処理フロー:
        1. Memory データの収集（イベント、戦略、メタデータ）
        2. JSON シリアライズ
        3. 圧縮（オプション）
        4. S3 アップロード（暗号化オプション）
        5. バックアップメタデータの返却
        
        Args:
            tool_parameters: ツールパラメータ辞書
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
        """
        # パラメータの取得
        memory_id = tool_parameters.get("memory_id", "").strip()
        backup_location = tool_parameters.get("backup_location", "").strip()
        include_events = tool_parameters.get("include_events", True)
        include_strategies = tool_parameters.get("include_strategies", True)
        compression = tool_parameters.get("compression", "gzip")
        encryption = tool_parameters.get("encryption", True)
        
        # 必須パラメータのチェック
        if not memory_id:
            yield self.create_text_message("❌ memory_id パラメータは必須です")
            return
        
        if not backup_location:
            yield self.create_text_message("❌ backup_location パラメータは必須です")
            return
        
        try:
            # ステップ1: Memory データの収集
            yield self.create_text_message(f"📦 Memory {memory_id} からデータを収集中...")
            
            collected_data = self._collect_memory_data(memory_id)
            
            # include_events と include_strategies のフラグに応じてデータを調整
            if not include_events:
                collected_data["events"] = []
            
            if not include_strategies:
                collected_data["strategies"] = []
            
            event_count = len(collected_data["events"])
            
            yield self.create_text_message(
                f"✅ データ収集完了: {event_count}件のイベント、"
                f"{len(collected_data.get('strategies', []))}個の戦略"
            )
            
            # ステップ2: バックアップデータ構造の作成
            backup_data = {
                "version": "1.0",
                "backup_metadata": self._create_backup_metadata(
                    memory_id=memory_id,
                    event_count=event_count,
                    compressed=(compression == "gzip"),
                    encrypted=encryption,
                ),
                "memory_config": {
                    "strategies": collected_data.get("strategies", []),
                    "tags": collected_data.get("tags", {}),
                },
                "events": collected_data["events"],
            }
            
            # memory_name と description も追加
            backup_data["backup_metadata"]["memory_name"] = collected_data.get("memory_name", "")
            backup_data["backup_metadata"]["description"] = collected_data.get("description", "")
            
            # ステップ3: JSON シリアライズと圧縮
            yield self.create_text_message("🔄 データをシリアライズ中...")
            
            compress = (compression == "gzip")
            serialized_data = BackupUtils.serialize_to_json(backup_data, compress=compress)
            
            file_size = len(serialized_data)
            backup_data["backup_metadata"]["file_size"] = file_size
            
            yield self.create_text_message(
                f"✅ シリアライズ完了: {BackupUtils.format_file_size(file_size)}"
            )
            
            # ステップ4: S3 アップロード
            yield self.create_text_message(f"☁️ S3 にアップロード中: {backup_location}")
            
            upload_result = self._upload_to_s3(
                data=serialized_data,
                s3_path=backup_location,
                encrypt=encryption
            )
            
            yield self.create_text_message("✅ S3 アップロード完了")
            
            # ステップ5: 結果の返却
            result = {
                "status": "success",
                "backup_location": backup_location,
                "metadata": backup_data["backup_metadata"],
            }
            
            yield self.create_json_message(result)
            
            # サマリーメッセージ
            summary = (
                f"✅ バックアップが正常に完了しました\n\n"
                f"📍 保存先: {backup_location}\n"
                f"📊 イベント数: {event_count}件\n"
                f"💾 ファイルサイズ: {BackupUtils.format_file_size(file_size)}\n"
                f"🗜️ 圧縮: {'有効' if compress else '無効'}\n"
                f"🔒 暗号化: {'有効' if encryption else '無効'}"
            )
            yield self.create_text_message(summary)
            
        except Exception as exc:
            logger.error(f"Backup failed: {exc}", exc_info=True)
            error_message = f"❌ バックアップに失敗しました: {str(exc)}"
            yield self.create_text_message(error_message)

    def _parse_s3_uri(self, s3_uri: str) -> tuple[str, str]:
        """
        S3 URI をバケット名とキーに分解
        
        Args:
            s3_uri: S3 URI (s3://bucket/path/to/file.json)
            
        Returns:
            (バケット名, キー) のタプル
            
        Raises:
            ValueError: 無効な S3 URI の場合
        """
        if not s3_uri.startswith("s3://"):
            raise ValueError(f"無効な S3 URI です: {s3_uri}（s3:// で始まる必要があります）")
        
        # s3:// を除去
        path = s3_uri[5:]
        
        # バケット名とキーに分割
        parts = path.split("/", 1)
        if len(parts) < 2:
            raise ValueError(f"無効な S3 URI です: {s3_uri}（バケット名とキーが必要です）")
        
        bucket = parts[0]
        key = parts[1]
        
        if not bucket or not key:
            raise ValueError(f"無効な S3 URI です: {s3_uri}（バケット名とキーが空です）")
        
        return bucket, key

    def _generate_backup_filename(self, memory_id: str) -> str:
        """
        バックアップファイル名を生成
        
        Args:
            memory_id: Memory ID
            
        Returns:
            バックアップファイル名（タイムスタンプと Memory ID を含む）
        """
        from datetime import datetime, timezone
        
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"backup_{memory_id}_{timestamp}.json.gz"
        
        return filename

    def _upload_to_s3(
        self, data: bytes, s3_path: str, encrypt: bool = True
    ) -> dict[str, Any]:
        """
        バックアップデータを S3 にアップロード
        
        Args:
            data: アップロードするデータ（バイト列）
            s3_path: S3 URI (s3://bucket/path/to/file.json)
            encrypt: サーバーサイド暗号化を使用するか
            
        Returns:
            アップロード結果辞書
            
        Raises:
            Exception: アップロードに失敗した場合
        """
        try:
            # S3 URI を解析
            bucket, key = self._parse_s3_uri(s3_path)
            
            # アップロードパラメータ
            put_params = {
                "Bucket": bucket,
                "Key": key,
                "Body": data,
            }
            
            # 暗号化オプション
            if encrypt:
                put_params["ServerSideEncryption"] = "AES256"
            
            # S3 にアップロード
            response = self.s3_client.put_object(**put_params)
            
            result = {
                "bucket": bucket,
                "key": key,
                "size": len(data),
                "etag": response.get("ETag", ""),
                "encrypted": encrypt,
            }
            
            logger.info(f"Uploaded backup to s3://{bucket}/{key}")
            
            return result
            
        except ClientError as error:
            error_code = error.response.get("Error", {}).get("Code", "Unknown")
            error_message = error.response.get("Error", {}).get("Message", str(error))
            raise Exception(f"S3 アップロードに失敗しました ({error_code}): {error_message}")
        except Exception as exc:
            logger.error(f"Failed to upload to S3: {exc}")
            raise

    def _download_from_s3(self, s3_path: str) -> bytes:
        """
        S3 からバックアップデータをダウンロード
        
        Args:
            s3_path: S3 URI (s3://bucket/path/to/file.json)
            
        Returns:
            ダウンロードされたデータ（バイト列）
            
        Raises:
            Exception: ダウンロードに失敗した場合
        """
        try:
            # S3 URI を解析
            bucket, key = self._parse_s3_uri(s3_path)
            
            # S3 からダウンロード
            response = self.s3_client.get_object(Bucket=bucket, Key=key)
            
            # データを読み込み
            data = response["Body"].read()
            
            logger.info(f"Downloaded backup from s3://{bucket}/{key} ({len(data)} bytes)")
            
            return data
            
        except ClientError as error:
            error_code = error.response.get("Error", {}).get("Code", "Unknown")
            error_message = error.response.get("Error", {}).get("Message", str(error))
            
            # よくあるエラーに対して分かりやすいメッセージを提供
            if error_code == "NoSuchKey":
                raise Exception(f"S3 バックアップファイルが見つかりません: {s3_path}")
            elif error_code == "NoSuchBucket":
                bucket, _ = self._parse_s3_uri(s3_path)
                raise Exception(f"S3 バケットが見つかりません: {bucket}")
            elif error_code == "AccessDenied":
                raise Exception(f"S3 バックアップファイルへのアクセスが拒否されました: {s3_path}")
            else:
                raise Exception(f"S3 ダウンロードに失敗しました ({error_code}): {error_message}")
        except Exception as exc:
            logger.error(f"Failed to download from S3: {exc}")
            raise

    def _deserialize_backup_data(self, data: bytes, compressed: bool) -> dict[str, Any]:
        """
        バックアップデータをデシリアライズ
        
        Args:
            data: バックアップデータ（バイト列）
            compressed: 圧縮されているか
            
        Returns:
            デシリアライズされたバックアップデータ
            
        Raises:
            Exception: デシリアライズに失敗した場合
        """
        try:
            # BackupUtils を使用してデシリアライズ
            backup_data = BackupUtils.deserialize_from_json(data, compressed=compressed)
            
            logger.info(f"Deserialized backup data: version={backup_data.get('version')}, "
                       f"events={len(backup_data.get('events', []))}")
            
            return backup_data
            
        except Exception as exc:
            logger.error(f"Failed to deserialize backup data: {exc}")
            raise Exception(f"バックアップデータのデシリアライズに失敗しました: {str(exc)}")

    def _apply_conflict_resolution(
        self, existing_events: list[dict], new_events: list[dict], strategy: str
    ) -> list[dict]:
        """
        競合解決戦略を適用
        
        Args:
            existing_events: 既存のイベントリスト
            new_events: 新しいイベントリスト
            strategy: 競合解決戦略（"skip", "overwrite", "merge"）
            
        Returns:
            競合解決後のイベントリスト
        """
        if strategy == "skip":
            # 既存のイベントを保持し、新しいイベントはスキップ
            # 実際には、既存の Memory にイベントを追加しないことで実現
            return []
        
        elif strategy == "overwrite":
            # 既存のイベントを削除し、新しいイベントで上書き
            # 注意: AgentCore Memory API には削除機能がないため、
            # 実際には新しい Memory を作成することを推奨
            return new_events
        
        elif strategy == "merge":
            # 既存のイベントと新しいイベントをマージ
            # タイムスタンプでソートして統合
            all_events = existing_events + new_events
            
            # タイムスタンプでソート（古い順）
            all_events.sort(key=lambda e: e.get("metadata", {}).get("createdAt", ""))
            
            return all_events
        
        else:
            # デフォルトは skip
            logger.warning(f"Unknown conflict resolution strategy: {strategy}, using 'skip'")
            return []

    def _batch_create_events(
        self, memory_id: str, events: list[dict], batch_size: int = 50
    ) -> dict[str, Any]:
        """
        イベントをバッチで作成
        
        Args:
            memory_id: ターゲット Memory ID
            events: 作成するイベントのリスト
            batch_size: バッチサイズ（デフォルト: 50）
            
        Returns:
            作成結果の辞書
            - success: 成功したイベント数
            - failed: 失敗したイベント数
            - errors: エラー詳細のリスト
        """
        results = {
            "success": 0,
            "failed": 0,
            "errors": []
        }
        
        total_events = len(events)
        logger.info(f"Starting batch create for {total_events} events")
        
        for i in range(0, total_events, batch_size):
            batch = events[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            
            logger.info(f"Processing batch {batch_num}: events {i+1}-{min(i+batch_size, total_events)}")
            
            for event in batch:
                try:
                    # イベントを作成
                    # 注意: create_event API はイベントの構造に依存します
                    # バックアップデータのイベント構造をそのまま使用
                    self.memory_client.create_event(
                        memory_id=memory_id,
                        actor_id=event.get("actorId", ""),
                        session_id=event.get("sessionId", ""),
                        namespace=event.get("namespace", ""),
                        messages=event.get("messages", []),
                    )
                    results["success"] += 1
                    
                except ClientError as error:
                    results["failed"] += 1
                    error_code = error.response.get("Error", {}).get("Code", "Unknown")
                    error_message = error.response.get("Error", {}).get("Message", str(error))
                    
                    error_detail = {
                        "event_id": event.get("eventId", "unknown"),
                        "error_code": error_code,
                        "error_message": error_message,
                    }
                    results["errors"].append(error_detail)
                    
                    logger.error(f"Failed to create event: {error_detail}")
                    
                except Exception as exc:
                    results["failed"] += 1
                    error_detail = {
                        "event_id": event.get("eventId", "unknown"),
                        "error": str(exc),
                    }
                    results["errors"].append(error_detail)
                    
                    logger.error(f"Failed to create event: {error_detail}")
        
        logger.info(f"Batch create completed: {results['success']} success, {results['failed']} failed")
        
        return results

    # ------------------------------------------------------------------
    # リストア操作
    # ------------------------------------------------------------------
    def _restore_memory(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        """
        バックアップから Memory リソースをリストア
        
        処理フロー:
        1. S3 からバックアップファイルをダウンロード
        2. 解凍（必要な場合）
        3. JSON デシリアライズ
        4. 構造検証
        5. ターゲット Memory の作成または取得
        6. 競合解決戦略の適用
        7. イベントのバッチ作成
        8. リストア結果の返却
        
        Args:
            tool_parameters: ツールパラメータ辞書
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
        """
        # パラメータの取得
        backup_location = tool_parameters.get("backup_location", "").strip()
        target_memory_id = tool_parameters.get("target_memory_id", "").strip()
        overwrite = tool_parameters.get("overwrite", False)
        conflict_resolution = tool_parameters.get("conflict_resolution", "skip")
        
        # 必須パラメータのチェック
        if not backup_location:
            yield self.create_text_message("❌ backup_location パラメータは必須です")
            return
        
        # 競合解決戦略の検証
        valid_strategies = ["skip", "overwrite", "merge"]
        if conflict_resolution not in valid_strategies:
            yield self.create_text_message(
                f"❌ 無効な競合解決戦略です: {conflict_resolution}\n"
                f"有効な戦略: {', '.join(valid_strategies)}"
            )
            return
        
        try:
            # ステップ1: S3 からバックアップファイルをダウンロード
            yield self.create_text_message(f"📥 S3 からバックアップをダウンロード中: {backup_location}")
            
            backup_data_bytes = self._download_from_s3(backup_location)
            
            yield self.create_text_message(
                f"✅ ダウンロード完了: {BackupUtils.format_file_size(len(backup_data_bytes))}"
            )
            
            # ステップ2-3: デシリアライズ（圧縮されている場合は解凍も含む）
            yield self.create_text_message("🔄 バックアップデータをデシリアライズ中...")
            
            # ファイル名から圧縮形式を推測
            compressed = backup_location.endswith(".gz")
            
            backup_data = self._deserialize_backup_data(backup_data_bytes, compressed=compressed)
            
            yield self.create_text_message("✅ デシリアライズ完了")
            
            # ステップ4: 構造検証
            yield self.create_text_message("🔍 バックアップデータの構造を検証中...")
            
            is_valid, error_message = BackupUtils.validate_backup_structure(backup_data)
            
            if not is_valid:
                yield self.create_text_message(f"❌ バックアップデータの検証に失敗しました: {error_message}")
                return
            
            yield self.create_text_message("✅ バックアップデータの検証完了")
            
            # バックアップメタデータの取得
            metadata = backup_data.get("backup_metadata", {})
            source_memory_id = metadata.get("memory_id", "unknown")
            event_count = metadata.get("event_count", 0)
            
            yield self.create_text_message(
                f"📊 バックアップ情報:\n"
                f"  - ソース Memory ID: {source_memory_id}\n"
                f"  - イベント数: {event_count}件\n"
                f"  - 作成日時: {metadata.get('created_at', 'unknown')}"
            )
            
            # ステップ5: ターゲット Memory の決定
            if not target_memory_id:
                # ターゲット Memory が指定されていない場合は、ソース Memory ID を使用
                target_memory_id = source_memory_id
                yield self.create_text_message(
                    f"ℹ️ ターゲット Memory ID が指定されていないため、"
                    f"ソース Memory ID を使用します: {target_memory_id}"
                )
            
            # Memory の存在確認
            memory_exists = False
            existing_events = []
            
            try:
                memory_details = self.memory_client.get_memory(memory_id=target_memory_id)
                memory_exists = True
                
                yield self.create_text_message(
                    f"ℹ️ ターゲット Memory は既に存在します: {target_memory_id}"
                )
                
                # 既存のイベントを取得（競合解決のため）
                if conflict_resolution == "merge":
                    yield self.create_text_message("📥 既存のイベントを取得中...")
                    existing_data = self._collect_memory_data(target_memory_id)
                    existing_events = existing_data.get("events", [])
                    yield self.create_text_message(f"✅ 既存イベント数: {len(existing_events)}件")
                
            except ClientError as error:
                error_code = error.response.get("Error", {}).get("Code", "Unknown")
                
                if error_code == "ResourceNotFoundException":
                    # Memory が存在しない場合は新規作成
                    yield self.create_text_message(
                        f"ℹ️ ターゲット Memory が存在しないため、新規作成します: {target_memory_id}"
                    )
                    
                    # Memory を作成
                    memory_config = backup_data.get("memory_config", {})
                    
                    try:
                        self.memory_client.create_memory(
                            memory_id=target_memory_id,
                            name=metadata.get("memory_name", target_memory_id),
                            description=metadata.get("description", "Restored from backup"),
                            strategies=memory_config.get("strategies", []),
                            tags=memory_config.get("tags", {}),
                        )
                        
                        yield self.create_text_message(f"✅ Memory を作成しました: {target_memory_id}")
                        
                    except Exception as create_error:
                        yield self.create_text_message(
                            f"❌ Memory の作成に失敗しました: {str(create_error)}"
                        )
                        return
                else:
                    # その他のエラー
                    context = AgentCoreError.create_error_context(
                        operation="get_memory",
                        memory_id=target_memory_id
                    )
                    error_message = AgentCoreError.handle_client_error(error, context)
                    yield self.create_text_message(f"❌ {error_message}")
                    return
            
            # ステップ6: 競合解決戦略の適用
            events_to_restore = backup_data.get("events", [])
            
            if memory_exists and conflict_resolution != "overwrite":
                yield self.create_text_message(
                    f"🔄 競合解決戦略を適用中: {conflict_resolution}"
                )
                
                events_to_restore = self._apply_conflict_resolution(
                    existing_events=existing_events,
                    new_events=events_to_restore,
                    strategy=conflict_resolution
                )
                
                if conflict_resolution == "skip":
                    # skip の場合は、新しいイベントを追加しない
                    yield self.create_text_message(
                        "ℹ️ 競合解決戦略が 'skip' のため、既存のイベントを保持します"
                    )
                    
                    result = {
                        "status": "success",
                        "target_memory_id": target_memory_id,
                        "results": {
                            "total_events": event_count,
                            "restored_events": 0,
                            "skipped_events": event_count,
                            "failed_events": 0,
                        },
                    }
                    
                    yield self.create_json_message(result)
                    yield self.create_text_message(
                        f"✅ リストア完了（スキップ）\n"
                        f"  - スキップされたイベント: {event_count}件"
                    )
                    return
            
            # ステップ7: イベントのバッチ作成
            if events_to_restore:
                yield self.create_text_message(
                    f"📝 {len(events_to_restore)}件のイベントをリストア中..."
                )
                
                batch_results = self._batch_create_events(
                    memory_id=target_memory_id,
                    events=events_to_restore
                )
                
                # ステップ8: リストア結果の返却
                restored_count = batch_results["success"]
                failed_count = batch_results["failed"]
                
                # ステータスの決定
                if failed_count == 0:
                    status = "success"
                elif restored_count > 0:
                    status = "partial_success"
                else:
                    status = "error"
                
                result = {
                    "status": status,
                    "target_memory_id": target_memory_id,
                    "results": {
                        "total_events": len(events_to_restore),
                        "restored_events": restored_count,
                        "skipped_events": 0,
                        "failed_events": failed_count,
                    },
                }
                
                # エラーがある場合は追加
                if batch_results["errors"]:
                    result["errors"] = batch_results["errors"]
                
                yield self.create_json_message(result)
                
                # サマリーメッセージ
                if status == "success":
                    summary = (
                        f"✅ リストアが正常に完了しました\n\n"
                        f"📍 ターゲット Memory ID: {target_memory_id}\n"
                        f"📊 リストアされたイベント: {restored_count}件"
                    )
                elif status == "partial_success":
                    summary = (
                        f"⚠️ リストアが部分的に完了しました\n\n"
                        f"📍 ターゲット Memory ID: {target_memory_id}\n"
                        f"✅ 成功: {restored_count}件\n"
                        f"❌ 失敗: {failed_count}件\n"
                        f"詳細なエラー情報は JSON レスポンスを参照してください"
                    )
                else:
                    summary = (
                        f"❌ リストアに失敗しました\n\n"
                        f"📍 ターゲット Memory ID: {target_memory_id}\n"
                        f"❌ 失敗: {failed_count}件\n"
                        f"詳細なエラー情報は JSON レスポンスを参照してください"
                    )
                
                yield self.create_text_message(summary)
            else:
                # リストアするイベントがない場合
                result = {
                    "status": "success",
                    "target_memory_id": target_memory_id,
                    "results": {
                        "total_events": 0,
                        "restored_events": 0,
                        "skipped_events": 0,
                        "failed_events": 0,
                    },
                }
                
                yield self.create_json_message(result)
                yield self.create_text_message(
                    f"✅ リストア完了（リストアするイベントがありません）"
                )
            
        except Exception as exc:
            logger.error(f"Restore failed: {exc}", exc_info=True)
            error_message = f"❌ リストアに失敗しました: {str(exc)}"
            yield self.create_text_message(error_message)

    # ------------------------------------------------------------------
    # バックアップ管理
    # ------------------------------------------------------------------
    def _list_backups(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        """
        指定された S3 パスのバックアップファイルを一覧表示
        
        処理フロー:
        1. S3 バケットとプレフィックスを解析
        2. S3 オブジェクトを列挙
        3. 各バックアップのメタデータを取得
        4. Memory ID でフィルタリング（オプション）
        5. 作成日時でソート
        
        Args:
            tool_parameters: ツールパラメータ辞書
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
        """
        # パラメータの取得
        backup_location = tool_parameters.get("backup_location", "").strip()
        memory_id_filter = tool_parameters.get("memory_id", "").strip()
        
        # 必須パラメータのチェック
        if not backup_location:
            yield self.create_text_message("❌ backup_location パラメータは必須です")
            return
        
        try:
            # ステップ1: S3 URI を解析
            yield self.create_text_message(f"🔍 バックアップを検索中: {backup_location}")
            
            # backup_location がディレクトリの場合（末尾が / の場合）
            if backup_location.endswith("/"):
                bucket, prefix = self._parse_s3_uri(backup_location)
            else:
                # ファイルパスの場合は、ディレクトリ部分を抽出
                bucket, key = self._parse_s3_uri(backup_location)
                # 最後の / までをプレフィックスとして使用
                if "/" in key:
                    prefix = key.rsplit("/", 1)[0] + "/"
                else:
                    prefix = ""
            
            # ステップ2: S3 オブジェクトを列挙
            logger.info(f"Listing backups in s3://{bucket}/{prefix}")
            
            backups = []
            continuation_token = None
            
            while True:
                # list_objects_v2 API を呼び出し
                list_params = {
                    "Bucket": bucket,
                    "Prefix": prefix,
                }
                
                if continuation_token:
                    list_params["ContinuationToken"] = continuation_token
                
                response = self.s3_client.list_objects_v2(**list_params)
                
                # オブジェクトを処理
                objects = response.get("Contents", [])
                
                for obj in objects:
                    key = obj.get("Key", "")
                    
                    # バックアップファイルのみを対象（.json または .json.gz）
                    if not (key.endswith(".json") or key.endswith(".json.gz")):
                        continue
                    
                    # ステップ3: 各バックアップのメタデータを取得
                    s3_uri = f"s3://{bucket}/{key}"
                    file_size = obj.get("Size", 0)
                    last_modified = obj.get("LastModified")
                    
                    # バックアップファイルをダウンロードしてメタデータを取得
                    # （パフォーマンスのため、ヘッダーのみを取得することも検討）
                    try:
                        # ファイルをダウンロード
                        backup_data_bytes = self._download_from_s3(s3_uri)
                        
                        # デシリアライズ
                        compressed = key.endswith(".gz")
                        backup_data = self._deserialize_backup_data(backup_data_bytes, compressed=compressed)
                        
                        # メタデータを抽出
                        metadata = backup_data.get("backup_metadata", {})
                        backup_memory_id = metadata.get("memory_id", "unknown")
                        event_count = metadata.get("event_count", 0)
                        created_at = metadata.get("created_at", "")
                        
                        # ステップ4: Memory ID でフィルタリング
                        if memory_id_filter and backup_memory_id != memory_id_filter:
                            continue
                        
                        # バックアップ情報を追加
                        backup_info = {
                            "backup_location": s3_uri,
                            "memory_id": backup_memory_id,
                            "created_at": created_at,
                            "file_size": file_size,
                            "event_count": event_count,
                        }
                        
                        backups.append(backup_info)
                        
                    except Exception as exc:
                        # メタデータの取得に失敗した場合は、基本情報のみを追加
                        logger.warning(f"Failed to get metadata for {s3_uri}: {exc}")
                        
                        backup_info = {
                            "backup_location": s3_uri,
                            "memory_id": "unknown",
                            "created_at": last_modified.isoformat() if last_modified else "",
                            "file_size": file_size,
                            "event_count": 0,
                            "error": "メタデータの取得に失敗しました",
                        }
                        
                        backups.append(backup_info)
                
                # 次のページがあるかチェック
                if response.get("IsTruncated"):
                    continuation_token = response.get("NextContinuationToken")
                else:
                    break
            
            # ステップ5: 作成日時でソート（新しい順）
            backups.sort(key=lambda b: b.get("created_at", ""), reverse=True)
            
            # 結果の返却
            result = {
                "status": "success",
                "backups": backups,
            }
            
            yield self.create_json_message(result)
            
            # サマリーメッセージ
            if backups:
                summary = f"✅ {len(backups)}件のバックアップが見つかりました\n\n"
                
                # 最初の5件を表示
                for i, backup in enumerate(backups[:5]):
                    summary += (
                        f"{i+1}. {backup['backup_location']}\n"
                        f"   Memory ID: {backup['memory_id']}\n"
                        f"   作成日時: {backup['created_at']}\n"
                        f"   イベント数: {backup['event_count']}件\n"
                        f"   ファイルサイズ: {BackupUtils.format_file_size(backup['file_size'])}\n\n"
                    )
                
                if len(backups) > 5:
                    summary += f"... 他 {len(backups) - 5}件のバックアップ\n"
                
                summary += "詳細は JSON レスポンスを参照してください"
            else:
                summary = "ℹ️ バックアップが見つかりませんでした"
                
                if memory_id_filter:
                    summary += f"\n（Memory ID フィルター: {memory_id_filter}）"
            
            yield self.create_text_message(summary)
            
        except ClientError as error:
            error_code = error.response.get("Error", {}).get("Code", "Unknown")
            error_message = error.response.get("Error", {}).get("Message", str(error))
            
            # よくあるエラーに対して分かりやすいメッセージを提供
            if error_code == "NoSuchBucket":
                yield self.create_text_message(f"❌ S3 バケットが見つかりません: {bucket}")
            elif error_code == "AccessDenied":
                yield self.create_text_message(f"❌ S3 バケットへのアクセスが拒否されました: {bucket}")
            else:
                yield self.create_text_message(
                    f"❌ S3 エラーが発生しました ({error_code}): {error_message}"
                )
        except Exception as exc:
            logger.error(f"List backups failed: {exc}", exc_info=True)
            error_message = f"❌ バックアップ一覧の取得に失敗しました: {str(exc)}"
            yield self.create_text_message(error_message)

    def _verify_backup(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        """
        バックアップファイルの整合性を検証
        
        処理フロー:
        1. S3 からバックアップファイルをダウンロード
        2. 解凍（必要な場合）
        3. JSON デシリアライズ
        4. 構造検証（必須フィールドの存在確認）
        5. データ整合性チェック（イベント数、タイムスタンプなど）
        
        Args:
            tool_parameters: ツールパラメータ辞書
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
        """
        # パラメータの取得
        backup_location = tool_parameters.get("backup_location", "").strip()
        
        # 必須パラメータのチェック
        if not backup_location:
            yield self.create_text_message("❌ backup_location パラメータは必須です")
            return
        
        try:
            # ステップ1: S3 からバックアップファイルをダウンロード
            yield self.create_text_message(f"📥 バックアップファイルをダウンロード中: {backup_location}")
            
            backup_data_bytes = self._download_from_s3(backup_location)
            
            yield self.create_text_message(
                f"✅ ダウンロード完了: {BackupUtils.format_file_size(len(backup_data_bytes))}"
            )
            
            # ステップ2-3: デシリアライズ（圧縮されている場合は解凍も含む）
            yield self.create_text_message("🔄 バックアップデータをデシリアライズ中...")
            
            # ファイル名から圧縮形式を推測
            compressed = backup_location.endswith(".gz")
            
            try:
                backup_data = self._deserialize_backup_data(backup_data_bytes, compressed=compressed)
                yield self.create_text_message("✅ デシリアライズ完了")
            except Exception as deserialize_error:
                # デシリアライズに失敗した場合
                result = {
                    "status": "invalid",
                    "validation_results": {
                        "structure_valid": False,
                        "data_consistent": False,
                        "event_count": 0,
                        "memory_id": "unknown",
                    },
                    "errors": [
                        {
                            "type": "deserialization_error",
                            "message": f"デシリアライズに失敗しました: {str(deserialize_error)}",
                        }
                    ],
                }
                
                yield self.create_json_message(result)
                yield self.create_text_message(
                    f"❌ バックアップファイルは無効です\n\n"
                    f"エラー: デシリアライズに失敗しました\n"
                    f"詳細: {str(deserialize_error)}"
                )
                return
            
            # ステップ4: 構造検証（必須フィールドの存在確認）
            yield self.create_text_message("🔍 バックアップデータの構造を検証中...")
            
            is_valid, error_message = BackupUtils.validate_backup_structure(backup_data)
            
            # バックアップメタデータの取得
            metadata = backup_data.get("backup_metadata", {})
            memory_id = metadata.get("memory_id", "unknown")
            event_count = metadata.get("event_count", 0)
            created_at = metadata.get("created_at", "unknown")
            
            # ステップ5: データ整合性チェック
            errors = []
            data_consistent = True
            
            if not is_valid:
                # 構造が無効な場合
                errors.append({
                    "type": "structure_validation_error",
                    "message": error_message,
                })
                data_consistent = False
            else:
                # 構造が有効な場合、追加の整合性チェックを実行
                yield self.create_text_message("🔍 データ整合性をチェック中...")
                
                # イベント配列の各イベントの基本的な構造をチェック
                events = backup_data.get("events", [])
                
                # イベントの必須フィールドをチェック（サンプリング）
                sample_size = min(10, len(events))  # 最初の10件をチェック
                
                for i, event in enumerate(events[:sample_size]):
                    # イベントが辞書であることを確認
                    if not isinstance(event, dict):
                        errors.append({
                            "type": "event_structure_error",
                            "message": f"イベント {i} が辞書ではありません",
                            "event_index": i,
                        })
                        data_consistent = False
                        continue
                    
                    # 基本的なフィールドの存在をチェック
                    # 注意: イベントの構造は Memory の戦略によって異なる可能性があるため、
                    # 厳密なチェックは行わない
                    if "actorId" not in event and "sessionId" not in event:
                        errors.append({
                            "type": "event_field_missing",
                            "message": f"イベント {i} に actorId または sessionId が不足しています",
                            "event_index": i,
                        })
                        data_consistent = False
                
                # memory_config の検証
                memory_config = backup_data.get("memory_config", {})
                
                if not isinstance(memory_config, dict):
                    errors.append({
                        "type": "config_structure_error",
                        "message": "memory_config が辞書ではありません",
                    })
                    data_consistent = False
                
                # strategies が配列であることを確認
                strategies = memory_config.get("strategies", [])
                if not isinstance(strategies, list):
                    errors.append({
                        "type": "strategies_structure_error",
                        "message": "strategies が配列ではありません",
                    })
                    data_consistent = False
                
                yield self.create_text_message("✅ データ整合性チェック完了")
            
            # 検証結果の作成
            if is_valid and data_consistent:
                status = "valid"
                summary_message = (
                    f"✅ バックアップファイルは有効です\n\n"
                    f"📍 バックアップ情報:\n"
                    f"  - Memory ID: {memory_id}\n"
                    f"  - イベント数: {event_count}件\n"
                    f"  - 作成日時: {created_at}\n"
                    f"  - ファイルサイズ: {BackupUtils.format_file_size(len(backup_data_bytes))}\n"
                    f"  - 圧縮: {'有効' if compressed else '無効'}"
                )
            else:
                status = "invalid"
                summary_message = (
                    f"❌ バックアップファイルは無効です\n\n"
                    f"📍 バックアップ情報:\n"
                    f"  - Memory ID: {memory_id}\n"
                    f"  - イベント数: {event_count}件\n"
                    f"  - エラー数: {len(errors)}件\n\n"
                    f"詳細なエラー情報は JSON レスポンスを参照してください"
                )
            
            result = {
                "status": status,
                "validation_results": {
                    "structure_valid": is_valid,
                    "data_consistent": data_consistent,
                    "event_count": event_count,
                    "memory_id": memory_id,
                    "created_at": created_at,
                    "file_size": len(backup_data_bytes),
                    "compressed": compressed,
                },
            }
            
            # エラーがある場合は追加
            if errors:
                result["errors"] = errors
            
            yield self.create_json_message(result)
            yield self.create_text_message(summary_message)
            
        except Exception as exc:
            logger.error(f"Verify backup failed: {exc}", exc_info=True)
            
            # エラーが発生した場合の結果
            result = {
                "status": "invalid",
                "validation_results": {
                    "structure_valid": False,
                    "data_consistent": False,
                    "event_count": 0,
                    "memory_id": "unknown",
                },
                "errors": [
                    {
                        "type": "verification_error",
                        "message": str(exc),
                    }
                ],
            }
            
            yield self.create_json_message(result)
            yield self.create_text_message(f"❌ バックアップ検証に失敗しました: {str(exc)}")

    # ------------------------------------------------------------------
    # Dify Tool エントリ
    # ------------------------------------------------------------------
    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage]:
        """
        バックアップ・リストア・一覧・検証の要求を受け、適切な操作を実行する
        
        Args:
            tool_parameters: ツールパラメータ辞書
            
        Yields:
            ToolInvokeMessage: 処理結果メッセージ
        """
        operation = tool_parameters.get("operation")
        valid_operations = {"backup", "restore", "list_backups", "verify_backup"}

        if operation not in valid_operations:
            yield self.create_text_message(
                f"❌ 無効な操作です: {operation}\n"
                f"有効な操作: {', '.join(valid_operations)}"
            )
            return

        # クライアントの初期化
        if not self._initialize_clients(tool_parameters):
            yield self.create_text_message(
                "❌ AWS クライアントの初期化に失敗しました"
            )
            return

        # 操作に応じた処理を実行
        if operation == "backup":
            yield from self._backup_memory(tool_parameters)
        elif operation == "restore":
            yield from self._restore_memory(tool_parameters)
        elif operation == "list_backups":
            yield from self._list_backups(tool_parameters)
        elif operation == "verify_backup":
            yield from self._verify_backup(tool_parameters)
