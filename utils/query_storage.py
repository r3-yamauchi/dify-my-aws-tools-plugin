"""
クエリストレージ

Memory検索クエリの永続化機構を提供します。

要件: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from .storage import BaseStorage, generate_unique_id

logger = logging.getLogger(__name__)


class QueryStorage:
    """クエリの保存と管理を行うクラス"""
    
    def __init__(self, storage: BaseStorage):
        """
        クエリストレージを初期化する
        
        Args:
            storage: ストレージインスタンス
            
        要件: 4.1
        """
        self.storage = storage
        self.key_prefix = "query"
        logger.info("QueryStorage initialized")
    
    def save_query(
        self,
        query: Dict[str, Any],
        name: str,
        description: str = "",
        query_id: Optional[str] = None
    ) -> str:
        """
        クエリを保存する
        
        Args:
            query: クエリオブジェクト
            name: クエリ名
            description: クエリの説明
            query_id: クエリID（指定しない場合は自動生成）
            
        Returns:
            str: クエリID
            
        要件: 4.1, 4.2
        """
        # クエリIDの生成または検証
        if query_id is None:
            query_id = generate_unique_id(prefix="q")
        
        # クエリ名の重複チェック
        existing_queries = self.list_queries()
        for existing_query in existing_queries:
            if existing_query.get("name") == name and existing_query.get("query_id") != query_id:
                raise ValueError(f"クエリ名が重複しています: {name}")
        
        # クエリデータの構築
        query_data = {
            "query_id": query_id,
            "name": name,
            "description": description,
            "query": query,
            "created_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }
        
        # ストレージに保存
        key = f"{self.key_prefix}_{query_id}"
        self.storage.save(key, query_data)
        
        logger.info(f"Query saved: {query_id} ({name})")
        return query_id
    
    def load_query(self, query_id: str) -> Optional[Dict[str, Any]]:
        """
        クエリを読み込む
        
        Args:
            query_id: クエリID
            
        Returns:
            Optional[Dict[str, Any]]: クエリデータ、存在しない場合は None
            
        要件: 4.3
        """
        key = f"{self.key_prefix}_{query_id}"
        query_data = self.storage.load(key)
        
        if query_data:
            logger.info(f"Query loaded: {query_id}")
        else:
            logger.warning(f"Query not found: {query_id}")
        
        return query_data
    
    def update_query(
        self,
        query_id: str,
        query: Optional[Dict[str, Any]] = None,
        name: Optional[str] = None,
        description: Optional[str] = None
    ) -> bool:
        """
        クエリを更新する
        
        Args:
            query_id: クエリID
            query: 新しいクエリオブジェクト（オプション）
            name: 新しいクエリ名（オプション）
            description: 新しい説明（オプション）
            
        Returns:
            bool: 更新が成功した場合 True
            
        要件: 4.6
        """
        # 既存のクエリを読み込む
        query_data = self.load_query(query_id)
        if not query_data:
            raise ValueError(f"クエリが見つかりません: {query_id}")
        
        # クエリ名の重複チェック（名前を変更する場合）
        if name and name != query_data.get("name"):
            existing_queries = self.list_queries()
            for existing_query in existing_queries:
                if existing_query.get("name") == name and existing_query.get("query_id") != query_id:
                    raise ValueError(f"クエリ名が重複しています: {name}")
        
        # データを更新
        if query is not None:
            query_data["query"] = query
        if name is not None:
            query_data["name"] = name
        if description is not None:
            query_data["description"] = description
        
        query_data["updated_at"] = datetime.utcnow().isoformat()
        
        # ストレージに保存
        key = f"{self.key_prefix}_{query_id}"
        self.storage.save(key, query_data)
        
        logger.info(f"Query updated: {query_id}")
        return True
    
    def delete_query(self, query_id: str) -> bool:
        """
        クエリを削除する
        
        Args:
            query_id: クエリID
            
        Returns:
            bool: 削除が成功した場合 True
            
        要件: 4.5
        """
        key = f"{self.key_prefix}_{query_id}"
        result = self.storage.delete(key)
        
        if result:
            logger.info(f"Query deleted: {query_id}")
        else:
            logger.warning(f"Query not found for deletion: {query_id}")
        
        return result
    
    def list_queries(self, name_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        クエリの一覧を取得する
        
        Args:
            name_filter: クエリ名のフィルター（部分一致）
            
        Returns:
            List[Dict[str, Any]]: クエリデータのリスト
            
        要件: 4.4
        """
        # すべてのクエリキーを取得
        keys = self.storage.list_keys(prefix=self.key_prefix)
        
        queries = []
        for key in keys:
            query_data = self.storage.load(key)
            if query_data:
                # 名前フィルターを適用
                if name_filter is None or name_filter in query_data.get("name", ""):
                    queries.append(query_data)
        
        # 作成日時の降順でソート
        queries.sort(key=lambda q: q.get("created_at", ""), reverse=True)
        
        logger.info(f"Listed {len(queries)} queries")
        return queries
    
    def export_query(self, query_id: str) -> Dict[str, Any]:
        """
        クエリをエクスポート用のJSON形式で取得する
        
        Args:
            query_id: クエリID
            
        Returns:
            Dict[str, Any]: エクスポート用のクエリデータ
            
        要件: 4.7
        """
        query_data = self.load_query(query_id)
        if not query_data:
            raise ValueError(f"クエリが見つかりません: {query_id}")
        
        logger.info(f"Query exported: {query_id}")
        return query_data
    
    def import_query(
        self,
        query_data: Dict[str, Any],
        overwrite: bool = False
    ) -> str:
        """
        クエリをインポートする
        
        Args:
            query_data: インポートするクエリデータ
            overwrite: 既存のクエリを上書きするかどうか
            
        Returns:
            str: インポートされたクエリID
            
        要件: 4.8
        """
        # 必須フィールドの検証
        required_fields = ["name", "query"]
        for field in required_fields:
            if field not in query_data:
                raise ValueError(f"必須フィールドが不足しています: {field}")
        
        # クエリIDの処理
        query_id = query_data.get("query_id")
        if query_id and self.storage.exists(f"{self.key_prefix}_{query_id}"):
            if not overwrite:
                raise ValueError(f"クエリIDが既に存在します: {query_id}。上書きする場合は overwrite=True を指定してください。")
        
        # クエリを保存
        query_id = self.save_query(
            query=query_data["query"],
            name=query_data["name"],
            description=query_data.get("description", ""),
            query_id=query_id
        )
        
        logger.info(f"Query imported: {query_id}")
        return query_id
    
    def query_exists(self, query_id: str) -> bool:
        """
        クエリが存在するかチェックする
        
        Args:
            query_id: クエリID
            
        Returns:
            bool: クエリが存在する場合 True
        """
        key = f"{self.key_prefix}_{query_id}"
        return self.storage.exists(key)
