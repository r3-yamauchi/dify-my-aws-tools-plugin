"""
テンプレートストレージ

Memoryテンプレートの永続化機構を提供します。

要件: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 7.1, 7.2, 7.3, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7
"""

import logging
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional

from .storage import BaseStorage, generate_unique_id

logger = logging.getLogger(__name__)


class TemplateStorage:
    """テンプレートの保存と管理を行うクラス"""
    
    def __init__(self, storage: BaseStorage):
        """
        テンプレートストレージを初期化する
        
        Args:
            storage: ストレージインスタンス
            
        要件: 5.1
        """
        self.storage = storage
        self.key_prefix = "template"
        self.version_prefix = "template_version"
        logger.info("TemplateStorage initialized")
    
    def save_template(
        self,
        strategies: List[Dict[str, Any]],
        namespaces: Optional[List[str]] = None,
        name: str = "",
        description: str = "",
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        template_id: Optional[str] = None
    ) -> str:
        """
        テンプレートを保存する
        
        Args:
            strategies: Memory戦略のリスト
            namespaces: Namespaceパターンのリスト
            name: テンプレート名
            description: テンプレートの説明
            tags: タグのリスト
            metadata: メタデータ
            template_id: テンプレートID（指定しない場合は自動生成）
            
        Returns:
            str: テンプレートID
            
        要件: 5.1, 5.2, 5.3, 5.4, 5.6, 5.7
        """
        # テンプレートIDの生成または検証
        if template_id is None:
            template_id = generate_unique_id(prefix="tmpl")
        
        # テンプレート名の重複チェック
        if name:
            existing_templates = self.list_templates()
            for existing_template in existing_templates:
                if existing_template.get("name") == name and existing_template.get("template_id") != template_id:
                    raise ValueError(f"テンプレート名が重複しています: {name}")
        
        # 既存のテンプレートがある場合はバージョンを保存
        is_update = self.template_exists(template_id)
        if is_update:
            existing_template = self.load_template(template_id)
            if existing_template:
                self._save_version(template_id, existing_template)
        
        # テンプレートデータの構築
        template_data = {
            "template_id": template_id,
            "name": name,
            "description": description,
            "version": "1.0.0",
            "strategies": strategies,
            "namespaces": namespaces or [],
            "tags": tags or [],
            "metadata": metadata or {},
            "created_at": datetime.now(UTC).isoformat() if not is_update else existing_template.get("created_at"),
            "updated_at": datetime.now(UTC).isoformat()
        }
        
        # ストレージに保存
        key = f"{self.key_prefix}_{template_id}"
        self.storage.save(key, template_data)
        
        logger.info(f"Template saved: {template_id} ({name})")
        return template_id
    
    def load_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        """
        テンプレートを読み込む
        
        Args:
            template_id: テンプレートID
            
        Returns:
            Optional[Dict[str, Any]]: テンプレートデータ、存在しない場合は None
            
        要件: 7.2
        """
        key = f"{self.key_prefix}_{template_id}"
        template_data = self.storage.load(key)
        
        if template_data:
            logger.info(f"Template loaded: {template_id}")
        else:
            logger.warning(f"Template not found: {template_id}")
        
        return template_data
    
    def update_template(
        self,
        template_id: str,
        strategies: Optional[List[Dict[str, Any]]] = None,
        namespaces: Optional[List[str]] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        テンプレートを更新する
        
        Args:
            template_id: テンプレートID
            strategies: 新しいMemory戦略のリスト（オプション）
            namespaces: 新しいNamespaceパターンのリスト（オプション）
            name: 新しいテンプレート名（オプション）
            description: 新しい説明（オプション）
            tags: 新しいタグのリスト（オプション）
            metadata: 新しいメタデータ（オプション）
            
        Returns:
            bool: 更新が成功した場合 True
            
        要件: 8.1
        """
        # 既存のテンプレートを読み込む
        template_data = self.load_template(template_id)
        if not template_data:
            raise ValueError(f"テンプレートが見つかりません: {template_id}")
        
        # 既存バージョンを保存
        self._save_version(template_id, template_data)
        
        # テンプレート名の重複チェック（名前を変更する場合）
        if name and name != template_data.get("name"):
            existing_templates = self.list_templates()
            for existing_template in existing_templates:
                if existing_template.get("name") == name and existing_template.get("template_id") != template_id:
                    raise ValueError(f"テンプレート名が重複しています: {name}")
        
        # データを更新
        if strategies is not None:
            template_data["strategies"] = strategies
        if namespaces is not None:
            template_data["namespaces"] = namespaces
        if name is not None:
            template_data["name"] = name
        if description is not None:
            template_data["description"] = description
        if tags is not None:
            template_data["tags"] = tags
        if metadata is not None:
            template_data["metadata"] = metadata
        
        template_data["updated_at"] = datetime.now(UTC).isoformat()
        
        # ストレージに保存
        key = f"{self.key_prefix}_{template_id}"
        self.storage.save(key, template_data)
        
        logger.info(f"Template updated: {template_id}")
        return True
    
    def delete_template(self, template_id: str) -> bool:
        """
        テンプレートを削除する
        
        Args:
            template_id: テンプレートID
            
        Returns:
            bool: 削除が成功した場合 True
        """
        key = f"{self.key_prefix}_{template_id}"
        result = self.storage.delete(key)
        
        # バージョン履歴も削除
        if result:
            self._delete_all_versions(template_id)
        
        if result:
            logger.info(f"Template deleted: {template_id}")
        else:
            logger.warning(f"Template not found for deletion: {template_id}")
        
        return result
    
    def list_templates(
        self,
        name_filter: Optional[str] = None,
        tag_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        テンプレートの一覧を取得する
        
        Args:
            name_filter: テンプレート名のフィルター（部分一致）
            tag_filter: タグのフィルター
            
        Returns:
            List[Dict[str, Any]]: テンプレートデータのリスト
        """
        # すべてのテンプレートキーを取得
        keys = self.storage.list_keys(prefix=self.key_prefix)
        
        templates = []
        for key in keys:
            # バージョンキーは除外
            if key.startswith(self.version_prefix):
                continue
            
            template_data = self.storage.load(key)
            if template_data:
                # 名前フィルターを適用
                if name_filter and name_filter not in template_data.get("name", ""):
                    continue
                
                # タグフィルターを適用
                if tag_filter and tag_filter not in template_data.get("tags", []):
                    continue
                
                templates.append(template_data)
        
        # 作成日時の降順でソート
        templates.sort(key=lambda t: t.get("created_at", ""), reverse=True)
        
        logger.info(f"Listed {len(templates)} templates")
        return templates
    
    def export_template(self, template_id: str) -> Dict[str, Any]:
        """
        テンプレートをエクスポート用のJSON形式で取得する
        
        Args:
            template_id: テンプレートID
            
        Returns:
            Dict[str, Any]: エクスポート用のテンプレートデータ
            
        要件: 7.1
        """
        template_data = self.load_template(template_id)
        if not template_data:
            raise ValueError(f"テンプレートが見つかりません: {template_id}")
        
        logger.info(f"Template exported: {template_id}")
        return template_data
    
    def import_template(
        self,
        template_data: Dict[str, Any],
        overwrite: bool = False
    ) -> str:
        """
        テンプレートをインポートする
        
        Args:
            template_data: インポートするテンプレートデータ
            overwrite: 既存のテンプレートを上書きするかどうか
            
        Returns:
            str: インポートされたテンプレートID
            
        要件: 7.2, 7.3
        """
        # 必須フィールドの検証（要件 7.3）
        required_fields = ["strategies"]
        for field in required_fields:
            if field not in template_data:
                raise ValueError(f"必須フィールドが不足しています: {field}")
        
        # テンプレートIDの処理
        template_id = template_data.get("template_id")
        if template_id and self.template_exists(template_id):
            if not overwrite:
                raise ValueError(
                    f"テンプレートIDが既に存在します: {template_id}。"
                    f"上書きする場合は overwrite=True を指定してください。"
                )
        
        # テンプレートを保存
        template_id = self.save_template(
            strategies=template_data["strategies"],
            namespaces=template_data.get("namespaces"),
            name=template_data.get("name", ""),
            description=template_data.get("description", ""),
            tags=template_data.get("tags"),
            metadata=template_data.get("metadata"),
            template_id=template_id
        )
        
        logger.info(f"Template imported: {template_id}")
        return template_id
    
    def template_exists(self, template_id: str) -> bool:
        """
        テンプレートが存在するかチェックする
        
        Args:
            template_id: テンプレートID
            
        Returns:
            bool: テンプレートが存在する場合 True
        """
        key = f"{self.key_prefix}_{template_id}"
        return self.storage.exists(key)
    
    # バージョン管理機能
    
    def _save_version(self, template_id: str, template_data: Dict[str, Any]) -> str:
        """
        テンプレートのバージョンを保存する
        
        Args:
            template_id: テンプレートID
            template_data: テンプレートデータ
            
        Returns:
            str: バージョンID
            
        要件: 8.1
        """
        version_id = generate_unique_id(prefix="ver")
        version_key = f"{self.version_prefix}_{template_id}_{version_id}"
        
        version_data = {
            **template_data,
            "version_id": version_id,
            "versioned_at": datetime.now(UTC).isoformat()
        }
        
        self.storage.save(version_key, version_data)
        logger.info(f"Template version saved: {template_id} -> {version_id}")
        return version_id
    
    def get_template_versions(self, template_id: str) -> List[Dict[str, Any]]:
        """
        テンプレートのバージョン履歴を取得する
        
        Args:
            template_id: テンプレートID
            
        Returns:
            List[Dict[str, Any]]: バージョンデータのリスト
            
        要件: 8.2
        """
        # バージョンキーを取得
        version_prefix = f"{self.version_prefix}_{template_id}"
        keys = self.storage.list_keys(prefix=version_prefix)
        
        versions = []
        for key in keys:
            version_data = self.storage.load(key)
            if version_data:
                versions.append(version_data)
        
        # バージョン作成日時の降順でソート
        versions.sort(key=lambda v: v.get("versioned_at", ""), reverse=True)
        
        logger.info(f"Listed {len(versions)} versions for template: {template_id}")
        return versions
    
    def get_template_version(self, template_id: str, version_id: str) -> Optional[Dict[str, Any]]:
        """
        特定のバージョンを取得する
        
        Args:
            template_id: テンプレートID
            version_id: バージョンID
            
        Returns:
            Optional[Dict[str, Any]]: バージョンデータ、存在しない場合は None
            
        要件: 8.3
        """
        version_key = f"{self.version_prefix}_{template_id}_{version_id}"
        version_data = self.storage.load(version_key)
        
        if version_data:
            logger.info(f"Template version loaded: {template_id} -> {version_id}")
        else:
            logger.warning(f"Template version not found: {template_id} -> {version_id}")
        
        return version_data
    
    def compare_versions(
        self,
        template_id: str,
        version1_id: str,
        version2_id: str
    ) -> Dict[str, Any]:
        """
        2つのバージョンを比較する
        
        Args:
            template_id: テンプレートID
            version1_id: バージョン1のID
            version2_id: バージョン2のID
            
        Returns:
            Dict[str, Any]: 差分情報
            
        要件: 8.4
        """
        version1 = self.get_template_version(template_id, version1_id)
        version2 = self.get_template_version(template_id, version2_id)
        
        if not version1:
            raise ValueError(f"バージョンが見つかりません: {version1_id}")
        if not version2:
            raise ValueError(f"バージョンが見つかりません: {version2_id}")
        
        # 差分を計算
        diff = {
            "version1_id": version1_id,
            "version2_id": version2_id,
            "differences": {}
        }
        
        # 主要フィールドの差分をチェック
        fields_to_compare = ["name", "description", "strategies", "namespaces", "tags", "metadata"]
        for field in fields_to_compare:
            val1 = version1.get(field)
            val2 = version2.get(field)
            if val1 != val2:
                diff["differences"][field] = {
                    "version1": val1,
                    "version2": val2
                }
        
        logger.info(f"Compared versions: {version1_id} vs {version2_id}")
        return diff
    
    def revert_to_version(self, template_id: str, version_id: str) -> bool:
        """
        指定されたバージョンに戻す
        
        Args:
            template_id: テンプレートID
            version_id: バージョンID
            
        Returns:
            bool: 復元が成功した場合 True
            
        要件: 8.5
        """
        # バージョンデータを取得
        version_data = self.get_template_version(template_id, version_id)
        if not version_data:
            raise ValueError(f"バージョンが見つかりません: {version_id}")
        
        # 現在のテンプレートをバージョンとして保存
        current_template = self.load_template(template_id)
        if current_template:
            self._save_version(template_id, current_template)
        
        # バージョンデータから不要なフィールドを除去
        restored_data = {k: v for k, v in version_data.items() if k not in ["version_id", "versioned_at"]}
        restored_data["updated_at"] = datetime.now(UTC).isoformat()
        
        # テンプレートを更新
        key = f"{self.key_prefix}_{template_id}"
        self.storage.save(key, restored_data)
        
        logger.info(f"Template reverted to version: {template_id} -> {version_id}")
        return True
    
    def tag_version(self, template_id: str, version_id: str, tag: str) -> bool:
        """
        バージョンにタグを付ける
        
        Args:
            template_id: テンプレートID
            version_id: バージョンID
            tag: タグ名
            
        Returns:
            bool: タグ付けが成功した場合 True
            
        要件: 8.6
        """
        version_data = self.get_template_version(template_id, version_id)
        if not version_data:
            raise ValueError(f"バージョンが見つかりません: {version_id}")
        
        # タグを追加
        if "version_tags" not in version_data:
            version_data["version_tags"] = []
        
        if tag not in version_data["version_tags"]:
            version_data["version_tags"].append(tag)
        
        # バージョンデータを保存
        version_key = f"{self.version_prefix}_{template_id}_{version_id}"
        self.storage.save(version_key, version_data)
        
        logger.info(f"Version tagged: {template_id} -> {version_id} ({tag})")
        return True
    
    def delete_version(self, template_id: str, version_id: str) -> bool:
        """
        バージョンを削除する
        
        Args:
            template_id: テンプレートID
            version_id: バージョンID
            
        Returns:
            bool: 削除が成功した場合 True
            
        要件: 8.7
        """
        version_key = f"{self.version_prefix}_{template_id}_{version_id}"
        result = self.storage.delete(version_key)
        
        if result:
            logger.info(f"Version deleted: {template_id} -> {version_id}")
        else:
            logger.warning(f"Version not found for deletion: {template_id} -> {version_id}")
        
        return result
    
    def _delete_all_versions(self, template_id: str) -> int:
        """
        テンプレートのすべてのバージョンを削除する
        
        Args:
            template_id: テンプレートID
            
        Returns:
            int: 削除されたバージョン数
        """
        version_prefix = f"{self.version_prefix}_{template_id}"
        keys = self.storage.list_keys(prefix=version_prefix)
        
        deleted_count = 0
        for key in keys:
            if self.storage.delete(key):
                deleted_count += 1
        
        logger.info(f"Deleted {deleted_count} versions for template: {template_id}")
        return deleted_count
