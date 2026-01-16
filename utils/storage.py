"""
ストレージ抽象化レイヤー

クエリとテンプレートの永続化機構を提供します。
ローカルストレージとS3ストレージの両方をサポートします。

要件: 21.1, 21.2, 21.3, 21.4, 21.5, 21.6, 21.7, 21.8
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class StorageError(Exception):
    """ストレージ操作のエラー"""
    
    def __init__(self, message: str, operation: str, context: Optional[Dict[str, Any]] = None):
        self.message = message
        self.operation = operation
        self.context = context or {}
        super().__init__(self.message)


class BaseStorage(ABC):
    """ストレージの抽象基底クラス"""
    
    @abstractmethod
    def save(self, key: str, data: Dict[str, Any]) -> bool:
        """
        データを保存する
        
        Args:
            key: データのキー（ID）
            data: 保存するデータ
            
        Returns:
            bool: 保存が成功した場合 True
            
        要件: 21.1
        """
        pass
    
    @abstractmethod
    def load(self, key: str) -> Optional[Dict[str, Any]]:
        """
        データを読み込む
        
        Args:
            key: データのキー（ID）
            
        Returns:
            Optional[Dict[str, Any]]: 読み込んだデータ、存在しない場合は None
            
        要件: 21.6
        """
        pass
    
    @abstractmethod
    def delete(self, key: str) -> bool:
        """
        データを削除する
        
        Args:
            key: データのキー（ID）
            
        Returns:
            bool: 削除が成功した場合 True
            
        要件: 21.7
        """
        pass
    
    @abstractmethod
    def list_keys(self, prefix: str = "") -> List[str]:
        """
        キーの一覧を取得する
        
        Args:
            prefix: キーのプレフィックス（フィルタリング用）
            
        Returns:
            List[str]: キーのリスト
            
        要件: 21.6
        """
        pass
    
    @abstractmethod
    def exists(self, key: str) -> bool:
        """
        データが存在するかチェックする
        
        Args:
            key: データのキー（ID）
            
        Returns:
            bool: データが存在する場合 True
            
        要件: 21.6
        """
        pass


class LocalStorage(BaseStorage):
    """ローカルファイルシステムを使用したストレージ"""
    
    def __init__(self, base_path: str = ".agentcore_storage"):
        """
        ローカルストレージを初期化する
        
        Args:
            base_path: ストレージのベースパス
            
        要件: 21.1, 21.4
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"LocalStorage initialized at {self.base_path}")
    
    def _get_file_path(self, key: str) -> Path:
        """キーからファイルパスを生成する"""
        # キーをサニタイズしてファイル名として安全にする
        safe_key = key.replace("/", "_").replace("\\", "_")
        return self.base_path / f"{safe_key}.json"
    
    def save(self, key: str, data: Dict[str, Any]) -> bool:
        """
        データをローカルファイルに保存する
        
        Args:
            key: データのキー（ID）
            data: 保存するデータ
            
        Returns:
            bool: 保存が成功した場合 True
            
        要件: 21.1
        """
        try:
            file_path = self._get_file_path(key)
            
            # タイムスタンプを追加
            data_with_metadata = {
                **data,
                "_storage_metadata": {
                    "saved_at": datetime.now(UTC).isoformat(),
                    "storage_type": "local",
                    "key": key
                }
            }
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data_with_metadata, f, ensure_ascii=False, indent=2)
            
            logger.info(f"Data saved to local storage: {key}")
            return True
            
        except Exception as exc:
            logger.error(f"Failed to save data to local storage: {exc}")
            raise StorageError(
                message=f"ローカルストレージへの保存に失敗しました: {exc}",
                operation="save",
                context={"key": key}
            )
    
    def load(self, key: str) -> Optional[Dict[str, Any]]:
        """
        データをローカルファイルから読み込む
        
        Args:
            key: データのキー（ID）
            
        Returns:
            Optional[Dict[str, Any]]: 読み込んだデータ、存在しない場合は None
            
        要件: 21.6
        """
        try:
            file_path = self._get_file_path(key)
            
            if not file_path.exists():
                logger.warning(f"Data not found in local storage: {key}")
                return None
            
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # ストレージメタデータを除去
            if "_storage_metadata" in data:
                del data["_storage_metadata"]
            
            logger.info(f"Data loaded from local storage: {key}")
            return data
            
        except json.JSONDecodeError as exc:
            logger.error(f"Failed to decode JSON from local storage: {exc}")
            raise StorageError(
                message=f"JSONデータの読み込みに失敗しました: {exc}",
                operation="load",
                context={"key": key}
            )
        except Exception as exc:
            logger.error(f"Failed to load data from local storage: {exc}")
            raise StorageError(
                message=f"ローカルストレージからの読み込みに失敗しました: {exc}",
                operation="load",
                context={"key": key}
            )
    
    def delete(self, key: str) -> bool:
        """
        データをローカルファイルから削除する
        
        Args:
            key: データのキー（ID）
            
        Returns:
            bool: 削除が成功した場合 True
            
        要件: 21.7
        """
        try:
            file_path = self._get_file_path(key)
            
            if not file_path.exists():
                logger.warning(f"Data not found in local storage: {key}")
                return False
            
            file_path.unlink()
            logger.info(f"Data deleted from local storage: {key}")
            return True
            
        except Exception as exc:
            logger.error(f"Failed to delete data from local storage: {exc}")
            raise StorageError(
                message=f"ローカルストレージからの削除に失敗しました: {exc}",
                operation="delete",
                context={"key": key}
            )
    
    def list_keys(self, prefix: str = "") -> List[str]:
        """
        キーの一覧を取得する
        
        Args:
            prefix: キーのプレフィックス（フィルタリング用）
            
        Returns:
            List[str]: キーのリスト
            
        要件: 21.6
        """
        try:
            keys = []
            for file_path in self.base_path.glob("*.json"):
                # ファイル名から.jsonを除去してキーを復元
                key = file_path.stem
                if not prefix or key.startswith(prefix):
                    keys.append(key)
            
            logger.info(f"Listed {len(keys)} keys from local storage")
            return keys
            
        except Exception as exc:
            logger.error(f"Failed to list keys from local storage: {exc}")
            raise StorageError(
                message=f"ローカルストレージのキー一覧取得に失敗しました: {exc}",
                operation="list_keys",
                context={"prefix": prefix}
            )
    
    def exists(self, key: str) -> bool:
        """
        データが存在するかチェックする
        
        Args:
            key: データのキー（ID）
            
        Returns:
            bool: データが存在する場合 True
            
        要件: 21.6
        """
        file_path = self._get_file_path(key)
        return file_path.exists()


class S3Storage(BaseStorage):
    """Amazon S3を使用したストレージ"""
    
    def __init__(self, bucket: str, prefix: str = "agentcore", region_name: str = "us-east-1"):
        """
        S3ストレージを初期化する
        
        Args:
            bucket: S3バケット名
            prefix: オブジェクトキーのプレフィックス
            region_name: AWSリージョン
            
        要件: 21.5
        """
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")
        self.region_name = region_name
        
        try:
            self.s3_client = boto3.client('s3', region_name=region_name)
            logger.info(f"S3Storage initialized: bucket={bucket}, prefix={prefix}")
        except Exception as exc:
            logger.error(f"Failed to initialize S3 client: {exc}")
            raise StorageError(
                message=f"S3クライアントの初期化に失敗しました: {exc}",
                operation="init",
                context={"bucket": bucket, "region": region_name}
            )
    
    def _get_s3_key(self, key: str) -> str:
        """キーからS3オブジェクトキーを生成する"""
        return f"{self.prefix}/{key}.json"
    
    def save(self, key: str, data: Dict[str, Any]) -> bool:
        """
        データをS3に保存する
        
        Args:
            key: データのキー（ID）
            data: 保存するデータ
            
        Returns:
            bool: 保存が成功した場合 True
            
        要件: 21.5, 22.4（サーバーサイド暗号化）
        """
        try:
            s3_key = self._get_s3_key(key)
            
            # タイムスタンプを追加
            data_with_metadata = {
                **data,
                "_storage_metadata": {
                    "saved_at": datetime.now(UTC).isoformat(),
                    "storage_type": "s3",
                    "bucket": self.bucket,
                    "key": key
                }
            }
            
            # JSONにシリアライズ
            json_data = json.dumps(data_with_metadata, ensure_ascii=False, indent=2)
            
            # S3にアップロード（サーバーサイド暗号化を有効化）
            self.s3_client.put_object(
                Bucket=self.bucket,
                Key=s3_key,
                Body=json_data.encode('utf-8'),
                ContentType='application/json',
                ServerSideEncryption='AES256'  # 要件 22.4
            )
            
            logger.info(f"Data saved to S3: s3://{self.bucket}/{s3_key}")
            return True
            
        except ClientError as exc:
            logger.error(f"Failed to save data to S3: {exc}")
            raise StorageError(
                message=f"S3への保存に失敗しました: {exc}",
                operation="save",
                context={"bucket": self.bucket, "key": key}
            )
        except Exception as exc:
            logger.error(f"Failed to save data to S3: {exc}")
            raise StorageError(
                message=f"S3への保存中に予期しないエラーが発生しました: {exc}",
                operation="save",
                context={"bucket": self.bucket, "key": key}
            )
    
    def load(self, key: str) -> Optional[Dict[str, Any]]:
        """
        データをS3から読み込む
        
        Args:
            key: データのキー（ID）
            
        Returns:
            Optional[Dict[str, Any]]: 読み込んだデータ、存在しない場合は None
            
        要件: 21.6
        """
        try:
            s3_key = self._get_s3_key(key)
            
            # S3からダウンロード
            response = self.s3_client.get_object(
                Bucket=self.bucket,
                Key=s3_key
            )
            
            # JSONをデコード
            json_data = response['Body'].read().decode('utf-8')
            data = json.loads(json_data)
            
            # ストレージメタデータを除去
            if "_storage_metadata" in data:
                del data["_storage_metadata"]
            
            logger.info(f"Data loaded from S3: s3://{self.bucket}/{s3_key}")
            return data
            
        except ClientError as exc:
            error_code = exc.response.get('Error', {}).get('Code', '')
            if error_code == 'NoSuchKey':
                logger.warning(f"Data not found in S3: {key}")
                return None
            else:
                logger.error(f"Failed to load data from S3: {exc}")
                raise StorageError(
                    message=f"S3からの読み込みに失敗しました: {exc}",
                    operation="load",
                    context={"bucket": self.bucket, "key": key}
                )
        except json.JSONDecodeError as exc:
            logger.error(f"Failed to decode JSON from S3: {exc}")
            raise StorageError(
                message=f"JSONデータの読み込みに失敗しました: {exc}",
                operation="load",
                context={"bucket": self.bucket, "key": key}
            )
        except Exception as exc:
            logger.error(f"Failed to load data from S3: {exc}")
            raise StorageError(
                message=f"S3からの読み込み中に予期しないエラーが発生しました: {exc}",
                operation="load",
                context={"bucket": self.bucket, "key": key}
            )
    
    def delete(self, key: str) -> bool:
        """
        データをS3から削除する
        
        Args:
            key: データのキー（ID）
            
        Returns:
            bool: 削除が成功した場合 True
            
        要件: 21.7
        """
        try:
            s3_key = self._get_s3_key(key)
            
            # S3から削除
            self.s3_client.delete_object(
                Bucket=self.bucket,
                Key=s3_key
            )
            
            logger.info(f"Data deleted from S3: s3://{self.bucket}/{s3_key}")
            return True
            
        except ClientError as exc:
            logger.error(f"Failed to delete data from S3: {exc}")
            raise StorageError(
                message=f"S3からの削除に失敗しました: {exc}",
                operation="delete",
                context={"bucket": self.bucket, "key": key}
            )
        except Exception as exc:
            logger.error(f"Failed to delete data from S3: {exc}")
            raise StorageError(
                message=f"S3からの削除中に予期しないエラーが発生しました: {exc}",
                operation="delete",
                context={"bucket": self.bucket, "key": key}
            )
    
    def list_keys(self, prefix: str = "") -> List[str]:
        """
        キーの一覧を取得する
        
        Args:
            prefix: キーのプレフィックス（フィルタリング用）
            
        Returns:
            List[str]: キーのリスト
            
        要件: 21.6
        """
        try:
            keys = []
            
            # S3オブジェクトを一覧取得
            list_prefix = f"{self.prefix}/"
            if prefix:
                list_prefix = f"{self.prefix}/{prefix}"
            
            paginator = self.s3_client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket, Prefix=list_prefix)
            
            for page in pages:
                if 'Contents' not in page:
                    continue
                
                for obj in page['Contents']:
                    s3_key = obj['Key']
                    # プレフィックスと.jsonを除去してキーを復元
                    if s3_key.startswith(f"{self.prefix}/") and s3_key.endswith('.json'):
                        key = s3_key[len(self.prefix)+1:-5]  # プレフィックスと.jsonを除去
                        keys.append(key)
            
            logger.info(f"Listed {len(keys)} keys from S3")
            return keys
            
        except ClientError as exc:
            logger.error(f"Failed to list keys from S3: {exc}")
            raise StorageError(
                message=f"S3のキー一覧取得に失敗しました: {exc}",
                operation="list_keys",
                context={"bucket": self.bucket, "prefix": prefix}
            )
        except Exception as exc:
            logger.error(f"Failed to list keys from S3: {exc}")
            raise StorageError(
                message=f"S3のキー一覧取得中に予期しないエラーが発生しました: {exc}",
                operation="list_keys",
                context={"bucket": self.bucket, "prefix": prefix}
            )
    
    def exists(self, key: str) -> bool:
        """
        データが存在するかチェックする
        
        Args:
            key: データのキー（ID）
            
        Returns:
            bool: データが存在する場合 True
            
        要件: 21.6
        """
        try:
            s3_key = self._get_s3_key(key)
            
            # head_objectでオブジェクトの存在を確認
            self.s3_client.head_object(
                Bucket=self.bucket,
                Key=s3_key
            )
            return True
            
        except ClientError as exc:
            error_code = exc.response.get('Error', {}).get('Code', '')
            if error_code == '404':
                return False
            else:
                logger.error(f"Failed to check existence in S3: {exc}")
                raise StorageError(
                    message=f"S3の存在確認に失敗しました: {exc}",
                    operation="exists",
                    context={"bucket": self.bucket, "key": key}
                )


class StorageFactory:
    """ストレージインスタンスを作成するファクトリークラス"""
    
    @staticmethod
    def create_storage(
        storage_type: str = "local",
        **kwargs
    ) -> BaseStorage:
        """
        ストレージインスタンスを作成する
        
        Args:
            storage_type: ストレージタイプ（"local" または "s3"）
            **kwargs: ストレージ固有のパラメータ
            
        Returns:
            BaseStorage: ストレージインスタンス
            
        要件: 21.3, 21.4
        """
        if storage_type == "local":
            base_path = kwargs.get("base_path", ".agentcore_storage")
            return LocalStorage(base_path=base_path)
        
        elif storage_type == "s3":
            bucket = kwargs.get("bucket")
            if not bucket:
                raise ValueError("S3ストレージにはbucketパラメータが必要です")
            
            prefix = kwargs.get("prefix", "agentcore")
            region_name = kwargs.get("region_name", "us-east-1")
            
            return S3Storage(
                bucket=bucket,
                prefix=prefix,
                region_name=region_name
            )
        
        else:
            raise ValueError(f"サポートされていないストレージタイプです: {storage_type}")


def generate_unique_id(prefix: str = "") -> str:
    """
    一意のIDを生成する
    
    Args:
        prefix: IDのプレフィックス
        
    Returns:
        str: 一意のID
        
    要件: 4.1, 5.7（一意のID生成）
    """
    unique_id = str(uuid4()).replace("-", "")[:16]
    if prefix:
        return f"{prefix}_{unique_id}"
    return unique_id
