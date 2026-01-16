"""
場所: tools/agentcore/agentcore_memory_template.py
内容: AgentCore Memory 設定テンプレートの作成・管理・適用を行うツール
目的: Memory の設定をテンプレートとして保存し、再利用可能にする

要件: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7
"""

import json
import logging
import os
import re
from collections.abc import Generator
from typing import Any, Dict, List, Optional
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from utils.utils import resolve_aws_credentials
    from utils.template_storage import TemplateStorage
    from utils.storage import LocalStorage, S3Storage
except ModuleNotFoundError:  # pragma: no cover
    from my_aws_tools.utils.utils import resolve_aws_credentials
    from my_aws_tools.utils.template_storage import TemplateStorage
    from my_aws_tools.utils.storage import LocalStorage, S3Storage

try:
    from bedrock_agentcore.memory import MemoryClient
    AGENTCORE_SDK_AVAILABLE = True
except ImportError as exc:  # pragma: no cover
    MemoryClient = None
    AGENTCORE_SDK_AVAILABLE = False
    print(f"Warning: bedrock-agentcore SDK import failed: {exc}")

try:
    import boto3
    BOTO3_AVAILABLE = True
except ImportError:  # pragma: no cover
    boto3 = None
    BOTO3_AVAILABLE = False

logger = logging.getLogger(__name__)


class AgentCoreMemoryTemplateTool(Tool):
    """Memory 設定テンプレートの作成・管理・適用を行うツール"""
    
    memory_client: Any = None
    template_storage: TemplateStorage = None
    s3_client: Any = None

    def _clean_id_parameter(self, value: str) -> str:
        """ID 文字列の前後にある引用符を取り除く"""
        if value and isinstance(value, str):
            value = value.strip()
            if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]
        return value
    
    def _initialize_clients(self, tool_parameters: Dict[str, Any]) -> bool:
        """
        MemoryClient、S3 Client、ストレージを初期化する
        
        Args:
            tool_parameters: ツールパラメータ
            
        Returns:
            bool: 初期化が成功した場合 True
            
        要件: 17.1, 17.2, 17.3, 17.4
        """
        try:
            # AWS 認証情報を解決
            credentials = resolve_aws_credentials(self, tool_parameters)
            aws_region = credentials.get("aws_region") or 'us-east-1'
            aws_access_key_id = credentials.get("aws_access_key_id")
            aws_secret_access_key = credentials.get("aws_secret_access_key")

            if AGENTCORE_SDK_AVAILABLE:
                # AK/SK が両方ある場合は環境変数経由で渡す
                if aws_access_key_id and aws_secret_access_key:
                    os.environ['AWS_ACCESS_KEY_ID'] = aws_access_key_id
                    os.environ['AWS_SECRET_ACCESS_KEY'] = aws_secret_access_key
                    os.environ['AWS_REGION'] = aws_region
                
                # MemoryClient を生成
                self.memory_client = MemoryClient(region_name=aws_region)
                logger.info(f"Memory client initialized for region: {aws_region}")
            else:
                logger.error("AgentCore Memory SDK not available")
                return False
            
            # S3 Client を初期化（S3 ストレージ使用時）
            if BOTO3_AVAILABLE:
                if aws_access_key_id and aws_secret_access_key:
                    self.s3_client = boto3.client(
                        's3',
                        region_name=aws_region,
                        aws_access_key_id=aws_access_key_id,
                        aws_secret_access_key=aws_secret_access_key
                    )
                else:
                    self.s3_client = boto3.client('s3', region_name=aws_region)
                logger.info("S3 client initialized")
            
            # TemplateStorage を初期化
            if not self.template_storage:
                storage_type = tool_parameters.get('storage_type', 'local')
                
                if storage_type == 's3':
                    s3_bucket = tool_parameters.get('s3_bucket')
                    s3_prefix = tool_parameters.get('s3_prefix', 'agentcore/templates/')
                    
                    if not s3_bucket:
                        logger.warning("S3 bucket not specified, falling back to local storage")
                        storage = LocalStorage()
                    else:
                        storage = S3Storage(
                            bucket_name=s3_bucket,
                            prefix=s3_prefix,
                            s3_client=self.s3_client
                        )
                else:
                    storage = LocalStorage()
                
                self.template_storage = TemplateStorage(storage)
                logger.info(f"Template storage initialized with {storage_type} storage")
            
            return True
                
        except Exception as e:
            logger.error(f"Failed to initialize clients: {str(e)}")
            return False

    def _create_template(self, tool_parameters: Dict[str, Any]) -> Dict[str, Any]:
        """
        テンプレートを作成する
        
        Args:
            tool_parameters: ツールパラメータ
            
        Returns:
            Dict[str, Any]: 作成されたテンプレートオブジェクト
            
        要件: 5.1, 5.2, 5.3, 5.4
        """
        template = {}
        
        # 戦略設定を記録
        strategies_param = tool_parameters.get('strategies')
        if strategies_param:
            # JSON文字列の場合はパース
            if isinstance(strategies_param, str):
                try:
                    strategies_param = json.loads(strategies_param)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse strategies JSON: {strategies_param}")
                    strategies_param = []
            
            template['strategies'] = strategies_param
        
        # Namespace パターンを記録
        namespaces_param = tool_parameters.get('namespaces')
        if namespaces_param:
            # JSON文字列の場合はパース
            if isinstance(namespaces_param, str):
                try:
                    namespaces_param = json.loads(namespaces_param)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse namespaces JSON: {namespaces_param}")
                    namespaces_param = []
            
            template['namespaces'] = namespaces_param
        
        # タグとメタデータを記録
        tags_param = tool_parameters.get('tags')
        if tags_param:
            if isinstance(tags_param, str):
                try:
                    tags_param = json.loads(tags_param)
                except json.JSONDecodeError:
                    # カンマ区切りの文字列として扱う
                    tags_param = [tag.strip() for tag in tags_param.split(',')]
            
            template['tags'] = tags_param
        
        metadata_param = tool_parameters.get('metadata')
        if metadata_param:
            if isinstance(metadata_param, str):
                try:
                    metadata_param = json.loads(metadata_param)
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse metadata JSON: {metadata_param}")
                    metadata_param = {}
            
            template['metadata'] = metadata_param
        
        logger.info(f"Template created with {len(template.get('strategies', []))} strategies")
        return template

    def _extract_template_from_memory(self, memory_id: str) -> Dict[str, Any]:
        """
        既存の Memory からテンプレートを抽出する
        
        Args:
            memory_id: Memory ID
            
        Returns:
            Dict[str, Any]: 抽出されたテンプレート
            
        要件: 5.5
        """
        try:
            if not self.memory_client:
                raise ValueError("Memory client が初期化されていません")
            
            # Memory の詳細を取得
            memory_details = self.memory_client.get_memory(memory_id=memory_id)
            
            # テンプレートを構築
            template = {}
            
            # 戦略設定を抽出
            if 'strategies' in memory_details:
                template['strategies'] = memory_details['strategies']
            
            # Namespace パターンを抽出
            if 'namespaces' in memory_details:
                template['namespaces'] = memory_details['namespaces']
            
            # その他のメタデータを抽出
            if 'tags' in memory_details:
                template['tags'] = memory_details['tags']
            
            logger.info(f"Template extracted from memory: {memory_id}")
            return template
            
        except Exception as e:
            logger.error(f"Failed to extract template from memory: {str(e)}")
            raise
    
    def _save_template(
        self,
        template: Dict[str, Any],
        name: str,
        description: str = ""
    ) -> str:
        """
        テンプレートを保存して ID を返す
        
        Args:
            template: テンプレートオブジェクト
            name: テンプレート名
            description: テンプレートの説明
            
        Returns:
            str: テンプレート ID
            
        要件: 5.6, 5.7, 5.8
        """
        try:
            if not self.template_storage:
                raise ValueError("Template storage が初期化されていません")
            
            template_id = self.template_storage.save_template(
                template=template,
                name=name,
                description=description
            )
            
            logger.info(f"Template saved: {name} (ID: {template_id})")
            return template_id
            
        except Exception as e:
            logger.error(f"Failed to save template: {str(e)}")
            raise

    def _load_template(self, template_id: str) -> Dict[str, Any]:
        """
        テンプレートを読み込む
        
        Args:
            template_id: テンプレート ID
            
        Returns:
            Dict[str, Any]: テンプレートデータ
            
        要件: 7.3
        """
        try:
            if not self.template_storage:
                raise ValueError("Template storage が初期化されていません")
            
            template_data = self.template_storage.load_template(template_id)
            
            if not template_data:
                raise ValueError(f"テンプレートが見つかりません: {template_id}")
            
            logger.info(f"Template loaded: {template_id}")
            return template_data
            
        except Exception as e:
            logger.error(f"Failed to load template: {str(e)}")
            raise
    
    def _substitute_namespace_variables(
        self,
        namespace: str,
        params: Dict[str, Any]
    ) -> str:
        """
        Namespace パターン内の変数を置換する
        
        Args:
            namespace: Namespace パターン（例: "/semantic/{actorId}/{sessionId}"）
            params: 置換パラメータ
            
        Returns:
            str: 置換後の Namespace
            
        要件: 6.3
        """
        result = namespace
        
        # {変数名} 形式の変数を置換
        for key, value in params.items():
            pattern = f"{{{key}}}"
            result = result.replace(pattern, str(value))
        
        logger.debug(f"Namespace substituted: {namespace} -> {result}")
        return result
    
    def _create_memory_from_template(
        self,
        template_id: str,
        params: Dict[str, Any],
        memory_name: Optional[str] = None
    ) -> str:
        """
        テンプレートから Memory を作成する
        
        Args:
            template_id: テンプレート ID
            params: Namespace 変数の置換パラメータ
            memory_name: Memory 名（指定しない場合は自動生成）
            
        Returns:
            str: 作成された Memory ID
            
        要件: 6.1, 6.2, 6.3, 6.4
        """
        try:
            # テンプレートを読み込む
            template_data = self._load_template(template_id)
            template = template_data.get('template', {})
            
            # Memory 名を決定
            if not memory_name:
                template_name = template_data.get('name', 'template')
                memory_name = f"{template_name}_{template_id[:8]}"
            
            # Namespace パターンを置換
            namespaces = template.get('namespaces', [])
            substituted_namespaces = []
            
            for namespace in namespaces:
                substituted = self._substitute_namespace_variables(namespace, params)
                substituted_namespaces.append(substituted)
            
            # Memory を作成
            if not self.memory_client:
                raise ValueError("Memory client が初期化されていません")
            
            # create_memory API を呼び出し
            create_params = {
                'name': memory_name,
                'strategies': template.get('strategies', [])
            }
            
            if substituted_namespaces:
                create_params['namespaces'] = substituted_namespaces
            
            result = self.memory_client.create_memory(**create_params)
            
            # Memory ID を取得
            memory_id = result.get('memory_id') if isinstance(result, dict) else str(result)
            
            logger.info(f"Memory created from template: {memory_id}")
            return memory_id
            
        except Exception as e:
            logger.error(f"Failed to create memory from template: {str(e)}")
            raise

    def _create_memories_batch(
        self,
        template_id: str,
        count: int,
        params_list: Optional[List[Dict[str, Any]]] = None,
        memory_name_prefix: Optional[str] = None
    ) -> List[str]:
        """
        テンプレートから複数の Memory を一括作成する
        
        Args:
            template_id: テンプレート ID
            count: 作成する Memory の数
            params_list: 各 Memory の Namespace 変数置換パラメータのリスト（指定しない場合は空の辞書を使用）
            memory_name_prefix: Memory 名のプレフィックス（指定しない場合は自動生成）
            
        Returns:
            List[str]: 作成された Memory ID のリスト
            
        要件: 6.6
        """
        try:
            memory_ids = []
            
            # params_list が指定されていない場合は空の辞書のリストを作成
            if params_list is None:
                params_list = [{} for _ in range(count)]
            
            # params_list の長さが count と一致しない場合は調整
            if len(params_list) < count:
                # 不足分を空の辞書で埋める
                params_list.extend([{} for _ in range(count - len(params_list))])
            elif len(params_list) > count:
                # 余分な要素を削除
                params_list = params_list[:count]
            
            # テンプレートを読み込んで名前を取得
            template_data = self._load_template(template_id)
            template_name = template_data.get('name', 'template')
            
            # Memory 名のプレフィックスを決定
            if not memory_name_prefix:
                memory_name_prefix = f"{template_name}_{template_id[:8]}"
            
            # 各 Memory を作成
            for i, params in enumerate(params_list):
                memory_name = f"{memory_name_prefix}_{i+1}"
                
                try:
                    memory_id = self._create_memory_from_template(
                        template_id=template_id,
                        params=params,
                        memory_name=memory_name
                    )
                    memory_ids.append(memory_id)
                except Exception as e:
                    logger.error(f"Failed to create memory {i+1}/{count}: {str(e)}")
                    # エラーが発生しても続行
                    continue
            
            logger.info(f"Batch created {len(memory_ids)}/{count} memories from template: {template_id}")
            return memory_ids
            
        except Exception as e:
            logger.error(f"Failed to batch create memories from template: {str(e)}")
            raise

    def _export_template(
        self,
        template_id: str,
        destination: str
    ) -> str:
        """
        テンプレートをエクスポートする
        
        Args:
            template_id: テンプレート ID
            destination: エクスポート先（"local" または "s3://bucket/key"）
            
        Returns:
            str: エクスポート先のパス
            
        要件: 7.1, 7.6
        """
        try:
            if not self.template_storage:
                raise ValueError("Template storage が初期化されていません")
            
            # テンプレートをエクスポート
            export_path = self.template_storage.export_template(
                template_id=template_id,
                destination=destination
            )
            
            logger.info(f"Template exported: {template_id} -> {export_path}")
            return export_path
            
        except Exception as e:
            logger.error(f"Failed to export template: {str(e)}")
            raise
    
    def _import_template(
        self,
        source: str,
        overwrite: bool = False
    ) -> str:
        """
        テンプレートをインポートする
        
        Args:
            source: インポート元（ファイルパスまたは "s3://bucket/key"）
            overwrite: 既存のテンプレートを上書きするかどうか
            
        Returns:
            str: インポートされたテンプレート ID
            
        要件: 7.2, 7.3, 7.4, 7.7, 7.8
        """
        try:
            if not self.template_storage:
                raise ValueError("Template storage が初期化されていません")
            
            # テンプレートをインポート
            template_id = self.template_storage.import_template(
                source=source,
                overwrite=overwrite
            )
            
            logger.info(f"Template imported: {source} -> {template_id}")
            return template_id
            
        except Exception as e:
            logger.error(f"Failed to import template: {str(e)}")
            raise
    
    def _list_templates(
        self,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        テンプレート一覧を取得する
        
        Args:
            filters: フィルター条件
            
        Returns:
            List[Dict[str, Any]]: テンプレートのリスト
        """
        try:
            if not self.template_storage:
                raise ValueError("Template storage が初期化されていません")
            
            templates = self.template_storage.list_templates(filters=filters)
            
            logger.info(f"Listed {len(templates)} templates")
            return templates
            
        except Exception as e:
            logger.error(f"Failed to list templates: {str(e)}")
            raise
    
    def _delete_template(self, template_id: str) -> bool:
        """
        テンプレートを削除する
        
        Args:
            template_id: テンプレート ID
            
        Returns:
            bool: 削除が成功した場合 True
        """
        try:
            if not self.template_storage:
                raise ValueError("Template storage が初期化されていません")
            
            result = self.template_storage.delete_template(template_id)
            
            logger.info(f"Template deleted: {template_id}")
            return result
            
        except Exception as e:
            logger.error(f"Failed to delete template: {str(e)}")
            raise

    def _get_template_versions(self, template_id: str) -> List[Dict[str, Any]]:
        """
        テンプレートのバージョン履歴を取得する
        
        Args:
            template_id: テンプレート ID
            
        Returns:
            List[Dict[str, Any]]: バージョンのリスト
            
        要件: 8.2
        """
        try:
            if not self.template_storage:
                raise ValueError("Template storage が初期化されていません")
            
            versions = self.template_storage.get_versions(template_id)
            
            logger.info(f"Retrieved {len(versions)} versions for template: {template_id}")
            return versions
            
        except Exception as e:
            logger.error(f"Failed to get template versions: {str(e)}")
            raise
    
    def _compare_versions(
        self,
        template_id: str,
        version1: str,
        version2: str
    ) -> Dict[str, Any]:
        """
        バージョン間の差分を取得する
        
        Args:
            template_id: テンプレート ID
            version1: バージョン1
            version2: バージョン2
            
        Returns:
            Dict[str, Any]: 差分情報
            
        要件: 8.4
        """
        try:
            if not self.template_storage:
                raise ValueError("Template storage が初期化されていません")
            
            diff = self.template_storage.compare_versions(
                template_id=template_id,
                version1=version1,
                version2=version2
            )
            
            logger.info(f"Compared versions {version1} and {version2} for template: {template_id}")
            return diff
            
        except Exception as e:
            logger.error(f"Failed to compare versions: {str(e)}")
            raise
    
    def _revert_to_version(
        self,
        template_id: str,
        version: str
    ) -> bool:
        """
        指定バージョンに戻す
        
        Args:
            template_id: テンプレート ID
            version: バージョン
            
        Returns:
            bool: 復元が成功した場合 True
            
        要件: 8.5
        """
        try:
            if not self.template_storage:
                raise ValueError("Template storage が初期化されていません")
            
            result = self.template_storage.revert_to_version(
                template_id=template_id,
                version=version
            )
            
            logger.info(f"Reverted template {template_id} to version {version}")
            return result
            
        except Exception as e:
            logger.error(f"Failed to revert to version: {str(e)}")
            raise

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
            operation = tool_parameters.get('operation', 'create')
            
            if operation == 'create':
                # テンプレートを作成
                template = self._create_template(tool_parameters)
                name = tool_parameters.get('template_name', '')
                description = tool_parameters.get('template_description', '')
                
                if not name:
                    yield self.create_text_message("❌ テンプレート名が必要です")
                    return
                
                template_id = self._save_template(template, name, description)
                
                response_data = {
                    'success': True,
                    'message': f"テンプレートを作成しました: {name}",
                    'data': {
                        'template_id': template_id,
                        'name': name,
                        'description': description
                    }
                }
                yield self.create_json_message(response_data)
            
            elif operation == 'create_from_memory':
                # 既存 Memory からテンプレートを作成
                memory_id = self._clean_id_parameter(tool_parameters.get('memory_id', ''))
                name = tool_parameters.get('template_name', '')
                description = tool_parameters.get('template_description', '')
                
                if not memory_id:
                    yield self.create_text_message("❌ Memory ID が必要です")
                    return
                
                if not name:
                    yield self.create_text_message("❌ テンプレート名が必要です")
                    return
                
                template = self._extract_template_from_memory(memory_id)
                template_id = self._save_template(template, name, description)
                
                response_data = {
                    'success': True,
                    'message': f"Memory からテンプレートを作成しました: {name}",
                    'data': {
                        'template_id': template_id,
                        'memory_id': memory_id,
                        'name': name,
                        'description': description
                    }
                }
                yield self.create_json_message(response_data)
            
            elif operation == 'apply':
                # テンプレートから Memory を作成
                template_id = self._clean_id_parameter(tool_parameters.get('template_id', ''))
                memory_name = tool_parameters.get('memory_name')
                
                if not template_id:
                    yield self.create_text_message("❌ テンプレート ID が必要です")
                    return
                
                # パラメータを取得
                params_str = tool_parameters.get('params', '{}')
                if isinstance(params_str, str):
                    try:
                        params = json.loads(params_str)
                    except json.JSONDecodeError:
                        yield self.create_text_message("❌ パラメータの JSON 解析に失敗しました")
                        return
                else:
                    params = params_str
                
                memory_id = self._create_memory_from_template(
                    template_id=template_id,
                    params=params,
                    memory_name=memory_name
                )
                
                response_data = {
                    'success': True,
                    'message': f"テンプレートから Memory を作成しました",
                    'data': {
                        'memory_id': memory_id,
                        'template_id': template_id,
                        'memory_name': memory_name
                    }
                }
                yield self.create_json_message(response_data)
            
            elif operation == 'apply_batch':
                # テンプレートから複数の Memory を一括作成
                template_id = self._clean_id_parameter(tool_parameters.get('template_id', ''))
                count = tool_parameters.get('count', 1)
                memory_name_prefix = tool_parameters.get('memory_name_prefix')
                
                if not template_id:
                    yield self.create_text_message("❌ テンプレート ID が必要です")
                    return
                
                if not isinstance(count, int) or count < 1:
                    yield self.create_text_message("❌ count は1以上の整数である必要があります")
                    return
                
                # パラメータリストを取得
                params_list_str = tool_parameters.get('params_list')
                params_list = None
                
                if params_list_str:
                    if isinstance(params_list_str, str):
                        try:
                            params_list = json.loads(params_list_str)
                        except json.JSONDecodeError:
                            yield self.create_text_message("❌ params_list の JSON 解析に失敗しました")
                            return
                    else:
                        params_list = params_list_str
                
                memory_ids = self._create_memories_batch(
                    template_id=template_id,
                    count=count,
                    params_list=params_list,
                    memory_name_prefix=memory_name_prefix
                )
                
                response_data = {
                    'success': True,
                    'message': f"テンプレートから {len(memory_ids)} 個の Memory を作成しました",
                    'data': {
                        'memory_ids': memory_ids,
                        'template_id': template_id,
                        'count': len(memory_ids),
                        'requested_count': count
                    }
                }
                yield self.create_json_message(response_data)

            elif operation == 'export':
                # テンプレートをエクスポート
                template_id = self._clean_id_parameter(tool_parameters.get('template_id', ''))
                destination = tool_parameters.get('destination', 'local')
                
                if not template_id:
                    yield self.create_text_message("❌ テンプレート ID が必要です")
                    return
                
                export_path = self._export_template(template_id, destination)
                
                response_data = {
                    'success': True,
                    'message': f"テンプレートをエクスポートしました",
                    'data': {
                        'template_id': template_id,
                        'export_path': export_path
                    }
                }
                yield self.create_json_message(response_data)
            
            elif operation == 'import':
                # テンプレートをインポート
                source = tool_parameters.get('source', '')
                overwrite = tool_parameters.get('overwrite', False)
                
                if not source:
                    yield self.create_text_message("❌ インポート元が必要です")
                    return
                
                template_id = self._import_template(source, overwrite)
                
                response_data = {
                    'success': True,
                    'message': f"テンプレートをインポートしました",
                    'data': {
                        'template_id': template_id,
                        'source': source
                    }
                }
                yield self.create_json_message(response_data)
            
            elif operation == 'list':
                # テンプレート一覧を取得
                filters_str = tool_parameters.get('filters')
                filters = None
                
                if filters_str:
                    if isinstance(filters_str, str):
                        try:
                            filters = json.loads(filters_str)
                        except json.JSONDecodeError:
                            yield self.create_text_message("❌ フィルターの JSON 解析に失敗しました")
                            return
                    else:
                        filters = filters_str
                
                templates = self._list_templates(filters)
                
                response_data = {
                    'success': True,
                    'message': f"{len(templates)} 件のテンプレートが見つかりました",
                    'data': {
                        'count': len(templates),
                        'templates': templates
                    }
                }
                yield self.create_json_message(response_data)
            
            elif operation == 'delete':
                # テンプレートを削除
                template_id = self._clean_id_parameter(tool_parameters.get('template_id', ''))
                
                if not template_id:
                    yield self.create_text_message("❌ テンプレート ID が必要です")
                    return
                
                result = self._delete_template(template_id)
                
                if result:
                    response_data = {
                        'success': True,
                        'message': f"テンプレートを削除しました: {template_id}",
                        'data': {
                            'template_id': template_id
                        }
                    }
                    yield self.create_json_message(response_data)
                else:
                    yield self.create_text_message(f"❌ テンプレートの削除に失敗しました: {template_id}")
            
            elif operation == 'versions':
                # バージョン履歴を取得
                template_id = self._clean_id_parameter(tool_parameters.get('template_id', ''))
                
                if not template_id:
                    yield self.create_text_message("❌ テンプレート ID が必要です")
                    return
                
                versions = self._get_template_versions(template_id)
                
                response_data = {
                    'success': True,
                    'message': f"{len(versions)} 件のバージョンが見つかりました",
                    'data': {
                        'template_id': template_id,
                        'count': len(versions),
                        'versions': versions
                    }
                }
                yield self.create_json_message(response_data)
            
            elif operation == 'compare':
                # バージョン比較
                template_id = self._clean_id_parameter(tool_parameters.get('template_id', ''))
                version1 = tool_parameters.get('version1', '')
                version2 = tool_parameters.get('version2', '')
                
                if not template_id:
                    yield self.create_text_message("❌ テンプレート ID が必要です")
                    return
                
                if not version1 or not version2:
                    yield self.create_text_message("❌ 比較する2つのバージョンが必要です")
                    return
                
                diff = self._compare_versions(template_id, version1, version2)
                
                response_data = {
                    'success': True,
                    'message': f"バージョン {version1} と {version2} を比較しました",
                    'data': {
                        'template_id': template_id,
                        'version1': version1,
                        'version2': version2,
                        'diff': diff
                    }
                }
                yield self.create_json_message(response_data)
            
            elif operation == 'revert':
                # バージョンを戻す
                template_id = self._clean_id_parameter(tool_parameters.get('template_id', ''))
                version = tool_parameters.get('version', '')
                
                if not template_id:
                    yield self.create_text_message("❌ テンプレート ID が必要です")
                    return
                
                if not version:
                    yield self.create_text_message("❌ バージョンが必要です")
                    return
                
                result = self._revert_to_version(template_id, version)
                
                if result:
                    response_data = {
                        'success': True,
                        'message': f"テンプレートをバージョン {version} に戻しました",
                        'data': {
                            'template_id': template_id,
                            'version': version
                        }
                    }
                    yield self.create_json_message(response_data)
                else:
                    yield self.create_text_message(f"❌ バージョンの復元に失敗しました")
            
            else:
                yield self.create_text_message(f"❌ 無効な操作です: {operation}")
        
        except Exception as e:
            logger.error(f"Invoke error: {str(e)}", exc_info=True)
            yield self.create_text_message(f"❌ 内部エラー: {str(e)}")
