"""
場所: tools/agentcore/agentcore_code_interpreter_files.py
内容: AgentCore Code Interpreter 環境のファイル操作を行うツール
目的: Code Interpreter へのファイルアップロード、ダウンロード、一覧取得、削除を提供

要件: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.8, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 12.1, 12.2, 12.3, 12.4, 12.5, 12.7, 22.1
"""

import json
import logging
import os
import base64
import mimetypes
from collections.abc import Generator
from typing import Any, Dict, List, Optional
from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

try:
    from utils.utils import resolve_aws_credentials
except ModuleNotFoundError:  # pragma: no cover
    from my_aws_tools.utils.utils import resolve_aws_credentials

try:
    from bedrock_agentcore.tools.code_interpreter_client import CodeInterpreter as CodeInterpreterClient
    AGENTCORE_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover
    CodeInterpreterClient = None
    AGENTCORE_SDK_AVAILABLE = False

try:
    import boto3
    BOTO3_AVAILABLE = True
except ImportError:  # pragma: no cover
    boto3 = None
    BOTO3_AVAILABLE = False

logger = logging.getLogger(__name__)

# ファイルサイズ制限（100MB）
MAX_FILE_SIZE = 100 * 1024 * 1024

# ファイル一覧の最大件数
MAX_FILE_LIST_SIZE = 1000


class AgentCoreCodeInterpreterFilesTool(Tool):
    """Code Interpreter 環境のファイル操作を行うツール"""
    
    code_interpreter_client: Any = None
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
        Code Interpreter Client と S3 Client を初期化する
        
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
                # CodeInterpreterClient は内部で boto3 を使用するため、
                # boto3 の標準認証チェーン（環境変数、~/.aws/credentials、IAMロールなど）が自動的に使用される
                self.code_interpreter_client = CodeInterpreterClient(region_name=aws_region)
                logger.info(f"Code Interpreter client initialized for region: {aws_region}")
            else:
                logger.error("AgentCore Code Interpreter SDK not available")
                return False
            
            # S3 Client を初期化
            if BOTO3_AVAILABLE:
                client_kwargs = build_boto3_client_kwargs(credentials)
                self.s3_client = boto3.client('s3', **client_kwargs)
                logger.info("S3 client initialized")
            
            return True
                
        except Exception as e:
            logger.error(f"Failed to initialize clients: {str(e)}")
            return False
    
    def _validate_file_size(self, file_size: int) -> bool:
        """
        ファイルサイズを検証する
        
        Args:
            file_size: ファイルサイズ（バイト）
            
        Returns:
            bool: サイズが制限内の場合 True
            
        Raises:
            ValueError: ファイルサイズが制限を超える場合
            
        要件: 20.3
        """
        if file_size > MAX_FILE_SIZE:
            raise ValueError(
                f"ファイルサイズが制限を超えています: {file_size} bytes "
                f"(最大: {MAX_FILE_SIZE} bytes = {MAX_FILE_SIZE // (1024 * 1024)} MB)"
            )
        return True
    
    def _scan_file_content(self, content: bytes, file_name: str) -> bool:
        """
        ファイル内容をスキャンする（セキュリティチェック）
        
        Args:
            content: ファイル内容
            file_name: ファイル名
            
        Returns:
            bool: スキャンが成功した場合 True
            
        Raises:
            ValueError: 不正なファイルタイプが検出された場合
            
        要件: 22.1, 22.8
        """
        # MIME タイプを推測
        mime_type, _ = mimetypes.guess_type(file_name)
        
        # 実行可能ファイルを拒否
        dangerous_extensions = ['.exe', '.bat', '.sh', '.cmd', '.com', '.scr', '.vbs', '.js']
        file_ext = os.path.splitext(file_name)[1].lower()
        
        if file_ext in dangerous_extensions:
            raise ValueError(f"実行可能ファイルのアップロードは許可されていません: {file_ext}")
        
        # 危険な MIME タイプを拒否
        dangerous_mime_types = ['application/x-executable', 'application/x-sh', 'application/x-bat']
        if mime_type in dangerous_mime_types:
            raise ValueError(f"危険な MIME タイプのファイルは許可されていません: {mime_type}")
        
        logger.info(f"File security scan passed: {file_name} (MIME: {mime_type})")
        return True

    def _upload_from_path(self, file_path: str, file_name: Optional[str] = None) -> str:
        """
        ローカルファイルをアップロードする
        
        Args:
            file_path: ローカルファイルパス
            file_name: アップロード時のファイル名（指定しない場合は元のファイル名）
            
        Returns:
            str: ファイル ID
            
        要件: 9.2, 9.4, 9.5
        """
        try:
            # ファイルの存在確認
            if not os.path.exists(file_path):
                raise ValueError(f"ファイルが見つかりません: {file_path}")
            
            # ファイルサイズを確認
            file_size = os.path.getsize(file_path)
            self._validate_file_size(file_size)
            
            # ファイル名を決定
            if not file_name:
                file_name = os.path.basename(file_path)
            
            # ファイルを読み込む
            with open(file_path, 'rb') as f:
                content = f.read()
            
            # セキュリティスキャン
            self._scan_file_content(content, file_name)
            
            # Code Interpreter にアップロード
            if not self.code_interpreter_client:
                raise ValueError("Code Interpreter client が初期化されていません")
            
            result = self.code_interpreter_client.upload_file(
                file_name=file_name,
                content=content
            )
            
            # ファイル ID を取得
            file_id = result.get('file_id') if isinstance(result, dict) else str(result)
            
            logger.info(f"File uploaded from path: {file_path} -> {file_id}")
            return file_id
            
        except Exception as e:
            logger.error(f"Failed to upload file from path: {str(e)}")
            raise
    
    def _upload_from_content(self, content: bytes, file_name: str) -> str:
        """
        ファイル内容を直接アップロードする
        
        Args:
            content: ファイル内容
            file_name: ファイル名
            
        Returns:
            str: ファイル ID
            
        要件: 9.3, 9.4
        """
        try:
            # ファイルサイズを確認
            file_size = len(content)
            self._validate_file_size(file_size)
            
            # セキュリティスキャン
            self._scan_file_content(content, file_name)
            
            # Code Interpreter にアップロード
            if not self.code_interpreter_client:
                raise ValueError("Code Interpreter client が初期化されていません")
            
            result = self.code_interpreter_client.upload_file(
                file_name=file_name,
                content=content
            )
            
            # ファイル ID を取得
            file_id = result.get('file_id') if isinstance(result, dict) else str(result)
            
            logger.info(f"File uploaded from content: {file_name} -> {file_id}")
            return file_id
            
        except Exception as e:
            logger.error(f"Failed to upload file from content: {str(e)}")
            raise
    
    def _upload_file(self, tool_parameters: Dict[str, Any]) -> Generator[ToolInvokeMessage]:
        """
        ファイルをアップロードする
        
        Args:
            tool_parameters: ツールパラメータ
            
        Yields:
            ToolInvokeMessage: アップロード結果
            
        要件: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7
        """
        try:
            file_path = tool_parameters.get('file_path')
            content_base64 = tool_parameters.get('content')
            file_name = tool_parameters.get('file_name')
            
            file_ids = []
            
            # 複数ファイルの一括アップロード
            if file_path and isinstance(file_path, list):
                for path in file_path:
                    file_id = self._upload_from_path(path)
                    file_ids.append(file_id)
            
            # 単一ファイルのアップロード（パスから）
            elif file_path:
                file_id = self._upload_from_path(file_path, file_name)
                file_ids.append(file_id)
            
            # 単一ファイルのアップロード（内容から）
            elif content_base64:
                if not file_name:
                    raise ValueError("ファイル名が必要です")
                
                # Base64 デコード
                content = base64.b64decode(content_base64)
                file_id = self._upload_from_content(content, file_name)
                file_ids.append(file_id)
            
            else:
                raise ValueError("file_path または content が必要です")
            
            response_data = {
                'success': True,
                'message': f"{len(file_ids)} 件のファイルをアップロードしました",
                'data': {
                    'file_ids': file_ids,
                    'count': len(file_ids)
                }
            }
            
            yield self.create_json_message(response_data)
            
        except ValueError as e:
            logger.error(f"Upload file error: {str(e)}")
            yield self.create_text_message(f"❌ ファイルアップロードエラー: {str(e)}")
        except Exception as e:
            logger.error(f"Upload file error: {str(e)}")
            yield self.create_text_message(f"❌ ファイルアップロードエラー: {str(e)}")

    def _download_file(self, tool_parameters: Dict[str, Any]) -> Generator[ToolInvokeMessage]:
        """
        実行結果ファイルをダウンロードする
        
        Args:
            tool_parameters: ツールパラメータ
            
        Yields:
            ToolInvokeMessage: ダウンロード結果
            
        要件: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6
        """
        try:
            file_id = self._clean_id_parameter(tool_parameters.get('file_id', ''))
            file_path = tool_parameters.get('file_path')
            
            if not file_id and not file_path:
                raise ValueError("file_id または file_path が必要です")
            
            if not self.code_interpreter_client:
                raise ValueError("Code Interpreter client が初期化されていません")
            
            # ファイルをダウンロード
            if file_id:
                result = self.code_interpreter_client.download_file(file_id=file_id)
            else:
                result = self.code_interpreter_client.download_file(file_path=file_path)
            
            # ファイル内容とメタデータを取得
            if isinstance(result, dict):
                content = result.get('content', b'')
                file_name = result.get('file_name', 'downloaded_file')
                file_size = result.get('file_size', len(content))
                mime_type = result.get('mime_type', 'application/octet-stream')
            else:
                content = result
                file_name = 'downloaded_file'
                file_size = len(content)
                mime_type = 'application/octet-stream'
            
            # Base64 エンコード
            content_base64 = base64.b64encode(content).decode('utf-8')
            
            response_data = {
                'success': True,
                'message': f"ファイルをダウンロードしました: {file_name}",
                'data': {
                    'file_name': file_name,
                    'file_size': file_size,
                    'mime_type': mime_type,
                    'content_base64': content_base64
                }
            }
            
            yield self.create_json_message(response_data)
            
        except ValueError as e:
            logger.error(f"Download file error: {str(e)}")
            yield self.create_text_message(f"❌ ファイルダウンロードエラー: {str(e)}")
        except Exception as e:
            logger.error(f"Download file error: {str(e)}")
            yield self.create_text_message(f"❌ ファイルダウンロードエラー: {str(e)}")
    
    def _filter_files(
        self,
        files: List[Dict[str, Any]],
        file_type: Optional[str] = None,
        pattern: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        ファイルをフィルタリングする
        
        Args:
            files: ファイルのリスト
            file_type: ファイルタイプ（拡張子）
            pattern: ファイル名パターン（部分一致）
            
        Returns:
            List[Dict[str, Any]]: フィルタリングされたファイルのリスト
            
        要件: 11.3, 11.4
        """
        filtered = files
        
        # ファイルタイプでフィルタリング
        if file_type:
            filtered = [
                f for f in filtered
                if f.get('file_name', '').endswith(file_type)
            ]
        
        # ファイル名パターンでフィルタリング
        if pattern:
            filtered = [
                f for f in filtered
                if pattern.lower() in f.get('file_name', '').lower()
            ]
        
        logger.info(f"Files filtered: {len(files)} -> {len(filtered)}")
        return filtered
    
    def _sort_files(
        self,
        files: List[Dict[str, Any]],
        sort_by: str = "name"
    ) -> List[Dict[str, Any]]:
        """
        ファイルをソートする
        
        Args:
            files: ファイルのリスト
            sort_by: ソートキー（name, size, created_at）
            
        Returns:
            List[Dict[str, Any]]: ソートされたファイルのリスト
            
        要件: 11.5
        """
        if sort_by == "name":
            sorted_files = sorted(files, key=lambda f: f.get('file_name', ''))
        elif sort_by == "size":
            sorted_files = sorted(files, key=lambda f: f.get('file_size', 0), reverse=True)
        elif sort_by == "created_at":
            sorted_files = sorted(files, key=lambda f: f.get('created_at', ''), reverse=True)
        else:
            sorted_files = files
        
        logger.info(f"Files sorted by: {sort_by}")
        return sorted_files
    
    def _paginate_files(
        self,
        files: List[Dict[str, Any]],
        page: int = 1,
        page_size: int = 20
    ) -> Dict[str, Any]:
        """
        ファイルをページネーションする
        
        Args:
            files: ファイルのリスト
            page: ページ番号（1から開始）
            page_size: ページサイズ
            
        Returns:
            Dict[str, Any]: ページネーション情報を含む結果
            
        要件: 11.6
        """
        total_count = len(files)
        total_pages = (total_count + page_size - 1) // page_size
        
        start_index = (page - 1) * page_size
        end_index = start_index + page_size
        
        paginated_files = files[start_index:end_index]
        
        return {
            'files': paginated_files,
            'page': page,
            'page_size': page_size,
            'total_count': total_count,
            'total_pages': total_pages,
            'has_next': page < total_pages,
            'has_prev': page > 1
        }

    def _list_files(self, tool_parameters: Dict[str, Any]) -> Generator[ToolInvokeMessage]:
        """
        ファイル一覧を取得する
        
        Args:
            tool_parameters: ツールパラメータ
            
        Yields:
            ToolInvokeMessage: ファイル一覧
            
        要件: 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 20.4
        """
        try:
            if not self.code_interpreter_client:
                raise ValueError("Code Interpreter client が初期化されていません")
            
            # ファイル一覧を取得
            result = self.code_interpreter_client.list_files()
            
            # ファイルリストを取得
            if isinstance(result, dict):
                files = result.get('files', [])
            elif isinstance(result, list):
                files = result
            else:
                files = []
            
            # 最大件数で制限
            if len(files) > MAX_FILE_LIST_SIZE:
                files = files[:MAX_FILE_LIST_SIZE]
                yield self.create_text_message(
                    f"⚠️ ファイル数が最大件数を超えています。最初の {MAX_FILE_LIST_SIZE} 件のみ表示します。"
                )
            
            # フィルタリング
            file_type = tool_parameters.get('file_type')
            pattern = tool_parameters.get('pattern')
            
            if file_type or pattern:
                files = self._filter_files(files, file_type, pattern)
            
            # ソート
            sort_by = tool_parameters.get('sort_by', 'name')
            files = self._sort_files(files, sort_by)
            
            # ページネーション
            page = tool_parameters.get('page', 1)
            page_size = tool_parameters.get('page_size', 20)
            
            paginated_result = self._paginate_files(files, page, page_size)
            
            response_data = {
                'success': True,
                'message': f"{paginated_result['total_count']} 件のファイルが見つかりました",
                'data': paginated_result
            }
            
            yield self.create_json_message(response_data)
            
        except Exception as e:
            logger.error(f"List files error: {str(e)}")
            yield self.create_text_message(f"❌ ファイル一覧取得エラー: {str(e)}")
    
    def _delete_file(self, tool_parameters: Dict[str, Any]) -> Generator[ToolInvokeMessage]:
        """
        ファイルを削除する
        
        Args:
            tool_parameters: ツールパラメータ
            
        Yields:
            ToolInvokeMessage: 削除結果
            
        要件: 12.1, 12.2, 12.3, 12.4, 12.5
        """
        try:
            file_id = self._clean_id_parameter(tool_parameters.get('file_id', ''))
            file_path = tool_parameters.get('file_path')
            file_ids = tool_parameters.get('file_ids')
            delete_all = tool_parameters.get('delete_all', False)
            
            if not self.code_interpreter_client:
                raise ValueError("Code Interpreter client が初期化されていません")
            
            deleted_count = 0
            
            # すべてのファイルを削除
            if delete_all:
                # 確認メッセージ
                yield self.create_text_message("⚠️ すべてのファイルを削除します。この操作は取り消せません。")
                
                # ファイル一覧を取得
                result = self.code_interpreter_client.list_files()
                files = result.get('files', []) if isinstance(result, dict) else result
                
                for file in files:
                    try:
                        fid = file.get('file_id') if isinstance(file, dict) else str(file)
                        self.code_interpreter_client.delete_file(file_id=fid)
                        deleted_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete file {fid}: {str(e)}")
            
            # 複数ファイルを一括削除
            elif file_ids:
                if isinstance(file_ids, str):
                    try:
                        file_ids = json.loads(file_ids)
                    except json.JSONDecodeError:
                        file_ids = [fid.strip() for fid in file_ids.split(',')]
                
                for fid in file_ids:
                    try:
                        self.code_interpreter_client.delete_file(file_id=fid)
                        deleted_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete file {fid}: {str(e)}")
                        yield self.create_text_message(f"⚠️ ファイルの削除に失敗しました: {fid}")
            
            # 単一ファイルを削除（ID指定）
            elif file_id:
                self.code_interpreter_client.delete_file(file_id=file_id)
                deleted_count = 1
            
            # 単一ファイルを削除（パス指定）
            elif file_path:
                self.code_interpreter_client.delete_file(file_path=file_path)
                deleted_count = 1
            
            else:
                raise ValueError("file_id、file_path、file_ids、または delete_all が必要です")
            
            response_data = {
                'success': True,
                'message': f"{deleted_count} 件のファイルを削除しました",
                'data': {
                    'deleted_count': deleted_count
                }
            }
            
            yield self.create_json_message(response_data)
            
        except ValueError as e:
            logger.error(f"Delete file error: {str(e)}")
            yield self.create_text_message(f"❌ ファイル削除エラー: {str(e)}")
        except Exception as e:
            logger.error(f"Delete file error: {str(e)}")
            yield self.create_text_message(f"❌ ファイル削除エラー: {str(e)}")

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
            operation = tool_parameters.get('operation', 'upload')
            
            if operation == 'upload':
                # ファイルをアップロード
                yield from self._upload_file(tool_parameters)
            
            elif operation == 'download':
                # ファイルをダウンロード
                yield from self._download_file(tool_parameters)
            
            elif operation == 'list':
                # ファイル一覧を取得
                yield from self._list_files(tool_parameters)
            
            elif operation == 'delete':
                # ファイルを削除
                yield from self._delete_file(tool_parameters)
            
            else:
                yield self.create_text_message(f"❌ 無効な操作です: {operation}")
        
        except Exception as e:
            logger.error(f"Invoke error: {str(e)}", exc_info=True)
            yield self.create_text_message(f"❌ 内部エラー: {str(e)}")
