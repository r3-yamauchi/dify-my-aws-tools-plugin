"""
AgentCore バックアップデータのシリアライズ・デシリアライズユーティリティ

JSON シリアライズ・デシリアライズ、gzip 圧縮・解凍機能を提供します。
"""

import json
import gzip
from typing import Dict, Any, Union
from datetime import datetime


class BackupUtils:
    """バックアップデータ処理のためのユーティリティクラス"""
    
    @staticmethod
    def serialize_to_json(data: Dict[str, Any], compress: bool = False) -> bytes:
        """
        バックアップデータを JSON 形式にシリアライズ
        
        Args:
            data: シリアライズするデータ（辞書形式）
            compress: gzip 圧縮を行うか（デフォルト: False）
            
        Returns:
            シリアライズされたデータ（バイト列）
            
        Raises:
            ValueError: シリアライズに失敗した場合
        """
        try:
            # JSON 文字列に変換（インデント付き、読みやすい形式）
            json_str = json.dumps(data, ensure_ascii=False, indent=2, default=BackupUtils._json_serializer)
            json_bytes = json_str.encode('utf-8')
            
            # 圧縮が有効な場合
            if compress:
                return BackupUtils.compress_data(json_bytes)
            
            return json_bytes
            
        except (TypeError, ValueError) as e:
            raise ValueError(f"JSON シリアライズに失敗しました: {str(e)}")
    
    @staticmethod
    def deserialize_from_json(data: bytes, compressed: bool = False) -> Dict[str, Any]:
        """
        バックアップデータを JSON 形式からデシリアライズ
        
        Args:
            data: デシリアライズするデータ（バイト列）
            compressed: gzip 圧縮されているか（デフォルト: False）
            
        Returns:
            デシリアライズされたデータ（辞書形式）
            
        Raises:
            ValueError: デシリアライズに失敗した場合
        """
        try:
            # 解凍が必要な場合
            if compressed:
                data = BackupUtils.decompress_data(data)
            
            # バイト列を文字列に変換
            json_str = data.decode('utf-8')
            
            # JSON をパース
            return json.loads(json_str)
            
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as e:
            raise ValueError(f"JSON デシリアライズに失敗しました: {str(e)}")
    
    @staticmethod
    def compress_data(data: bytes) -> bytes:
        """
        データを gzip 形式で圧縮
        
        Args:
            data: 圧縮するデータ（バイト列）
            
        Returns:
            圧縮されたデータ（バイト列）
            
        Raises:
            ValueError: 圧縮に失敗した場合
        """
        try:
            return gzip.compress(data, compresslevel=6)  # 圧縮レベル6（バランス重視）
        except Exception as e:
            raise ValueError(f"データの圧縮に失敗しました: {str(e)}")
    
    @staticmethod
    def decompress_data(data: bytes) -> bytes:
        """
        gzip 形式で圧縮されたデータを解凍
        
        Args:
            data: 解凍するデータ（バイト列）
            
        Returns:
            解凍されたデータ（バイト列）
            
        Raises:
            ValueError: 解凍に失敗した場合
        """
        try:
            return gzip.decompress(data)
        except Exception as e:
            raise ValueError(f"データの解凍に失敗しました: {str(e)}")
    
    @staticmethod
    def _json_serializer(obj: Any) -> Union[str, int, float, list, dict]:
        """
        JSON シリアライズのためのカスタムシリアライザー
        
        datetime オブジェクトなどを適切に変換します。
        
        Args:
            obj: シリアライズするオブジェクト
            
        Returns:
            シリアライズ可能な形式に変換されたオブジェクト
            
        Raises:
            TypeError: シリアライズできない型の場合
        """
        # datetime オブジェクトを ISO 8601 文字列に変換
        if isinstance(obj, datetime):
            return obj.isoformat()
        
        # その他のオブジェクトは文字列に変換を試みる
        if hasattr(obj, '__dict__'):
            return obj.__dict__
        
        raise TypeError(f"型 {type(obj)} は JSON シリアライズできません")
    
    @staticmethod
    def validate_backup_structure(data: Dict[str, Any]) -> tuple[bool, str]:
        """
        バックアップデータの構造を検証
        
        Args:
            data: 検証するバックアップデータ
            
        Returns:
            (検証結果, エラーメッセージ)
            - 検証結果: True（有効）または False（無効）
            - エラーメッセージ: 検証エラーの詳細（有効な場合は空文字列）
        """
        # 必須フィールドのチェック
        required_fields = ['version', 'backup_metadata', 'events']
        missing_fields = [field for field in required_fields if field not in data]
        
        if missing_fields:
            return False, f"必須フィールドが不足しています: {', '.join(missing_fields)}"
        
        # backup_metadata の必須フィールドをチェック
        metadata = data.get('backup_metadata', {})
        required_metadata_fields = ['created_at', 'memory_id', 'event_count']
        missing_metadata = [field for field in required_metadata_fields if field not in metadata]
        
        if missing_metadata:
            return False, f"バックアップメタデータに必須フィールドが不足しています: {', '.join(missing_metadata)}"
        
        # events が配列であることを確認
        if not isinstance(data.get('events'), list):
            return False, "events フィールドは配列である必要があります"
        
        # イベント数の整合性チェック
        actual_event_count = len(data['events'])
        declared_event_count = metadata.get('event_count', 0)
        
        if actual_event_count != declared_event_count:
            return False, f"イベント数が一致しません。宣言: {declared_event_count}件、実際: {actual_event_count}件"
        
        # バージョンの確認
        version = data.get('version')
        if not version:
            return False, "バージョン情報が不足しています"
        
        # サポートされているバージョンかチェック
        supported_versions = ['1.0']
        if version not in supported_versions:
            return False, f"サポートされていないバージョンです: {version}（サポート: {', '.join(supported_versions)}）"
        
        return True, ""
    
    @staticmethod
    def calculate_data_size(data: Union[bytes, Dict[str, Any]]) -> int:
        """
        データサイズを計算
        
        Args:
            data: サイズを計算するデータ（バイト列または辞書）
            
        Returns:
            データサイズ（バイト）
        """
        if isinstance(data, bytes):
            return len(data)
        elif isinstance(data, dict):
            # 辞書の場合は JSON シリアライズしてサイズを計算
            json_bytes = BackupUtils.serialize_to_json(data, compress=False)
            return len(json_bytes)
        else:
            raise ValueError(f"サポートされていないデータ型です: {type(data)}")
    
    @staticmethod
    def format_file_size(size_bytes: int) -> str:
        """
        ファイルサイズを人間が読みやすい形式でフォーマット
        
        Args:
            size_bytes: ファイルサイズ（バイト）
            
        Returns:
            フォーマットされたファイルサイズ文字列
        """
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.2f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.2f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
