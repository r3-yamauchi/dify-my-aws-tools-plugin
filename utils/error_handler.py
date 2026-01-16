"""
CloudWatch Logs エラーハンドリングユーティリティ

AWS API エラーの日本語メッセージ変換とパラメータ検証エラーのメッセージ生成を提供します。
"""

from botocore.exceptions import ClientError
from typing import Dict, Any, Optional


class CloudWatchLogsError:
    """CloudWatch Logs 関連のエラーハンドリングクラス"""
    
    # AWS API エラーコードと日本語メッセージのマッピング
    ERROR_MESSAGES = {
        # CloudWatch Logs 固有のエラー
        'ResourceNotFoundException': 'リソースが見つかりません。ロググループまたはログストリーム名を確認してください。',
        'InvalidParameterException': 'パラメータが無効です。入力値を確認してください。',
        'LimitExceededException': 'リクエスト制限を超えました。しばらく待ってから再試行してください。',
        'ServiceUnavailableException': 'CloudWatch Logs サービスが一時的に利用できません。しばらく待ってから再試行してください。',
        'ThrottlingException': 'API レート制限に達しました。リクエスト頻度を下げて再試行してください。',
        'InvalidSequenceTokenException': 'シーケンストークンが無効です。最新のトークンを使用してください。',
        'DataAlreadyAcceptedException': 'データは既に受け入れられています。',
        'InvalidTimeException': '指定された時刻が無効です。有効な時刻範囲を指定してください。',
        'MalformedQueryException': 'クエリの形式が正しくありません。フィルターパターンを確認してください。',
        
        # CloudWatch Logs Insight 固有のエラー
        'InvalidQueryException': 'Logs Insightクエリの構文が無効です。クエリ文字列を確認してください。',
        'QueryCompileException': 'クエリのコンパイルに失敗しました。クエリ構文を確認してください。',
        'QueryTimeoutException': 'クエリの実行がタイムアウトしました。クエリを簡素化するか、時間範囲を狭めてください。',
        'TooManyQueriesException': '同時実行クエリ数の上限に達しました。しばらく待ってから再試行してください。',
        'QueryExecutionException': 'クエリの実行中にエラーが発生しました。クエリ内容を確認してください。',
        'ResourceLimitExceededException': 'リソース制限を超えました。クエリの範囲を狭めて再試行してください。',
        
        # 一般的な AWS エラー
        'AccessDeniedException': 'CloudWatch Logs へのアクセス権限がありません。IAM ポリシーを確認してください。',
        'UnauthorizedOperation': '操作が許可されていません。必要な権限を確認してください。',
        'InvalidUserID.NotFound': 'ユーザーIDが見つかりません。',
        'AuthFailure': '認証に失敗しました。AWS 認証情報を確認してください。',
        'SignatureDoesNotMatch': 'AWS 署名が一致しません。認証情報を確認してください。',
        'TokenRefreshRequired': 'トークンの更新が必要です。',
        'RequestExpired': 'リクエストの有効期限が切れています。',
        'InvalidAccessKeyId': 'AWS アクセスキーIDが無効です。',
        'InvalidSecurityToken': 'セキュリティトークンが無効です。',
        
        # ネットワーク関連エラー
        'RequestTimeout': 'リクエストがタイムアウトしました。ネットワーク接続を確認してください。',
        'ServiceTimeout': 'サービスがタイムアウトしました。しばらく待ってから再試行してください。',
        'NetworkingError': 'ネットワークエラーが発生しました。接続を確認してください。',
        
        # その他の一般的なエラー
        'InternalError': '内部エラーが発生しました。しばらく待ってから再試行してください。',
        'InternalFailure': '内部的な障害が発生しました。しばらく待ってから再試行してください。',
        'ServiceFailure': 'サービスで障害が発生しました。しばらく待ってから再試行してください。',
        'ValidationException': '入力値の検証に失敗しました。パラメータを確認してください。',
    }
    
    # パラメータ検証エラーメッセージのテンプレート
    VALIDATION_ERROR_TEMPLATES = {
        'required_parameter': '{param_name} は必須パラメータです。',
        'invalid_format': '{param_name} の形式が無効です。{detail}',
        'out_of_range': '{param_name} が有効な範囲外です。{detail}',
        'invalid_value': '{param_name} の値が無効です。{detail}',
        'conflicting_parameters': 'パラメータの組み合わせが無効です。{detail}',
        'missing_dependency': '{param_name} を使用するには {dependency} が必要です。',
    }
    
    @staticmethod
    def handle_client_error(error: ClientError, context: Optional[Dict[str, Any]] = None) -> str:
        """
        AWS API エラーを適切な日本語メッセージに変換
        
        Args:
            error: boto3 ClientError
            context: エラーコンテキスト情報（オプション）
            
        Returns:
            日本語のエラーメッセージ
        """
        error_code = error.response.get('Error', {}).get('Code', 'UnknownError')
        error_message = error.response.get('Error', {}).get('Message', '')
        
        # 定義済みのエラーメッセージがあるかチェック
        if error_code in CloudWatchLogsError.ERROR_MESSAGES:
            base_message = CloudWatchLogsError.ERROR_MESSAGES[error_code]
        else:
            # 未定義のエラーコードの場合
            base_message = f'AWS API エラーが発生しました（{error_code}）'
        
        # コンテキスト情報を追加
        if context:
            context_info = []
            if 'log_group_name' in context:
                context_info.append(f"ロググループ: {context['log_group_name']}")
            if 'log_group_names' in context:
                context_info.append(f"ロググループ: {context['log_group_names']}")
            if 'log_stream_name' in context:
                context_info.append(f"ログストリーム: {context['log_stream_name']}")
            if 'query_id' in context:
                context_info.append(f"クエリID: {context['query_id']}")
            if 'query_string' in context:
                context_info.append(f"クエリ: {context['query_string']}")
            if 'operation' in context:
                context_info.append(f"操作: {context['operation']}")
            
            if context_info:
                base_message += f" ({', '.join(context_info)})"
        
        # 元のエラーメッセージが有用な場合は追加
        if error_message and error_message not in base_message:
            # 英語のエラーメッセージを簡潔に追加
            if len(error_message) < 200:  # 長すぎるメッセージは省略
                base_message += f" 詳細: {error_message}"
        
        return base_message
    
    @staticmethod
    def handle_validation_error(param_name: str, error_type: str, detail: str = "") -> str:
        """
        パラメータ検証エラーのメッセージを生成
        
        Args:
            param_name: パラメータ名
            error_type: エラータイプ（validation_error_templatesのキー）
            detail: 詳細情報（オプション）
            
        Returns:
            日本語のエラーメッセージ
        """
        if error_type not in CloudWatchLogsError.VALIDATION_ERROR_TEMPLATES:
            return f'{param_name} の検証でエラーが発生しました: {detail}'
        
        template = CloudWatchLogsError.VALIDATION_ERROR_TEMPLATES[error_type]
        
        # テンプレートに値を埋め込み
        try:
            return template.format(param_name=param_name, detail=detail)
        except KeyError:
            # テンプレートに必要なキーがない場合
            return template.format(param_name=param_name, detail=detail, dependency="")
    
    @staticmethod
    def create_parameter_error_context(operation: str, **params) -> Dict[str, Any]:
        """
        エラーコンテキスト情報を作成
        
        Args:
            operation: 実行していた操作名
            **params: パラメータ情報
            
        Returns:
            エラーコンテキスト辞書
        """
        context = {'operation': operation}
        
        # 重要なパラメータのみをコンテキストに含める
        important_params = [
            'log_group_name', 'log_stream_name', 'filter_pattern', 
            'query_id', 'query_string', 'log_group_names'
        ]
        for param in important_params:
            if param in params and params[param] is not None:
                context[param] = params[param]
        
        return context
    
    @staticmethod
    def format_aws_error_for_user(error: Exception, operation: str = "", **context) -> str:
        """
        AWS エラーをユーザー向けにフォーマット
        
        Args:
            error: 発生したエラー
            operation: 実行していた操作
            **context: コンテキスト情報
            
        Returns:
            ユーザー向けのエラーメッセージ
        """
        if isinstance(error, ClientError):
            error_context = CloudWatchLogsError.create_parameter_error_context(operation, **context)
            return CloudWatchLogsError.handle_client_error(error, error_context)
        elif isinstance(error, ValueError):
            # パラメータ検証エラーなど
            return f"入力値エラー: {str(error)}"
        elif isinstance(error, ConnectionError):
            return "AWS サービスへの接続に失敗しました。ネットワーク接続を確認してください。"
        elif isinstance(error, TimeoutError):
            return "リクエストがタイムアウトしました。しばらく待ってから再試行してください。"
        else:
            # その他の予期しないエラー
            return f"予期しないエラーが発生しました: {str(error)}"


class AgentCoreError:
    """AgentCore 関連のエラーハンドリングクラス"""
    
    # AWS API エラーコードと日本語メッセージのマッピング
    ERROR_MESSAGES = {
        # AgentCore Memory 固有のエラー
        'ResourceNotFoundException': 'リソースが見つかりません。Memory ID または Runtime ID を確認してください。',
        'InvalidParameterException': 'パラメータが無効です。入力値を確認してください。',
        'ValidationException': '入力値の検証に失敗しました。パラメータを確認してください。',
        'ThrottlingException': 'API レート制限に達しました。しばらく待ってから再試行してください。',
        'ServiceUnavailableException': 'AgentCore サービスが一時的に利用できません。しばらく待ってから再試行してください。',
        'ConflictException': 'リソースが競合状態です。しばらく待ってから再試行してください。',
        
        # Runtime 固有のエラー
        'InvocationNotFoundException': '実行が見つかりません。実行 ID を確認してください。',
        'AgentNotFoundException': 'Runtime が見つかりません。Runtime ID を確認してください。',
        
        # 認証関連エラー
        'AccessDeniedException': 'AgentCore へのアクセス権限がありません。IAM ポリシーを確認してください。',
        'UnauthorizedOperation': '操作が許可されていません。必要な権限を確認してください。',
        'AuthFailure': '認証に失敗しました。AWS 認証情報を確認してください。',
        
        # その他の一般的なエラー
        'InternalError': '内部エラーが発生しました。しばらく待ってから再試行してください。',
        'ServiceFailure': 'サービスで障害が発生しました。しばらく待ってから再試行してください。',
        'RequestTimeout': 'リクエストがタイムアウトしました。ネットワーク接続を確認してください。',
    }
    
    # バックアップ・リストア操作のエラーメッセージ
    BACKUP_ERROR_MESSAGES = {
        'backup_file_not_found': 'バックアップファイルが見つかりません。S3 パスを確認してください: {s3_path}',
        'backup_file_read_error': 'バックアップファイルの読み込みに失敗しました: {reason}',
        'backup_validation_error': 'バックアップデータの検証に失敗しました: {detail}',
        'backup_structure_invalid': 'バックアップファイルの構造が無効です。必須フィールド: {missing_fields}',
        'backup_decompression_error': 'バックアップファイルの解凍に失敗しました: {reason}',
        'backup_deserialization_error': 'バックアップデータのデシリアライズに失敗しました: {reason}',
        'backup_upload_error': 'S3 へのバックアップアップロードに失敗しました: {reason}',
        'backup_download_error': 'S3 からのバックアップダウンロードに失敗しました: {reason}',
        's3_access_error': 'S3 バケットへのアクセスに失敗しました。権限を確認してください: {bucket}',
        's3_uri_parse_error': 'S3 URI の解析に失敗しました。正しい形式で指定してください: {uri}',
    }
    
    # リストア操作のエラーメッセージ
    RESTORE_ERROR_MESSAGES = {
        'restore_target_not_found': 'リストア先の Memory が見つかりません: {memory_id}',
        'restore_event_creation_error': 'イベントの作成に失敗しました: {event_id}',
        'restore_partial_failure': 'リストアが部分的に失敗しました。成功: {success_count}件、失敗: {failed_count}件',
        'restore_conflict_resolution_error': '競合解決戦略の適用に失敗しました: {strategy}',
        'restore_memory_creation_error': '新しい Memory の作成に失敗しました: {reason}',
    }
    
    # マージ操作のエラーメッセージ
    MERGE_ERROR_MESSAGES = {
        'merge_source_not_found': 'マージ元の Memory が見つかりません: {memory_id}',
        'merge_target_not_found': 'マージ先の Memory が見つかりません: {memory_id}',
        'merge_event_collection_error': 'イベントの収集に失敗しました: {memory_id}',
        'merge_deduplication_error': '重複排除処理に失敗しました: {reason}',
        'merge_conflict_resolution_error': '競合解決処理に失敗しました: {strategy}',
        'merge_strategy_merge_error': '戦略のマージに失敗しました: {reason}',
        'merge_partial_failure': 'マージが部分的に失敗しました。成功: {success_count}件、失敗: {failed_count}件',
    }
    
    # 分割操作のエラーメッセージ
    SPLIT_ERROR_MESSAGES = {
        'split_source_not_found': '分割元の Memory が見つかりません: {memory_id}',
        'split_grouping_error': 'イベントのグループ化に失敗しました: {criteria}',
        'split_memory_creation_error': '分割先の Memory の作成に失敗しました: {group_name}',
        'split_event_copy_error': 'イベントのコピーに失敗しました: {reason}',
        'split_index_creation_error': '分割インデックスの作成に失敗しました: {reason}',
        'split_invalid_criteria': '無効な分割基準が指定されました: {criteria}',
        'split_partial_failure': '分割が部分的に失敗しました。成功: {success_count}個、失敗: {failed_count}個',
    }
    
    # イベントコピー操作のエラーメッセージ
    COPY_ERROR_MESSAGES = {
        'copy_source_not_found': 'コピー元の Memory が見つかりません: {memory_id}',
        'copy_target_not_found': 'コピー先の Memory が見つかりません: {memory_id}',
        'copy_filter_error': 'イベントのフィルタリングに失敗しました: {reason}',
        'copy_event_error': 'イベントのコピーに失敗しました: {event_id}',
        'copy_partial_failure': 'コピーが部分的に失敗しました。成功: {success_count}件、失敗: {failed_count}件',
    }
    
    @staticmethod
    def handle_client_error(error: ClientError, context: Optional[Dict[str, Any]] = None) -> str:
        """
        AWS API エラーを適切な日本語メッセージに変換
        
        Args:
            error: boto3 ClientError
            context: エラーコンテキスト情報（オプション）
            
        Returns:
            日本語のエラーメッセージ
        """
        error_code = error.response.get('Error', {}).get('Code', 'UnknownError')
        error_message = error.response.get('Error', {}).get('Message', '')
        
        # 定義済みのエラーメッセージがあるかチェック
        if error_code in AgentCoreError.ERROR_MESSAGES:
            base_message = AgentCoreError.ERROR_MESSAGES[error_code]
        else:
            base_message = f'AWS API エラーが発生しました（{error_code}）'
        
        # コンテキスト情報を追加
        if context:
            context_info = []
            if 'operation' in context:
                context_info.append(f"操作: {context['operation']}")
            if 'memory_id' in context:
                context_info.append(f"Memory ID: {context['memory_id']}")
            if 'runtime_id' in context:
                context_info.append(f"Runtime ID: {context['runtime_id']}")
            if 'event_id' in context:
                context_info.append(f"イベント ID: {context['event_id']}")
            if 'actor_id' in context:
                context_info.append(f"Actor ID: {context['actor_id']}")
            if 'session_id' in context:
                context_info.append(f"Session ID: {context['session_id']}")
            if 'invocation_id' in context:
                context_info.append(f"実行 ID: {context['invocation_id']}")
            
            if context_info:
                base_message += f" ({', '.join(context_info)})"
        
        # 元のエラーメッセージが有用な場合は追加
        if error_message and error_message not in base_message and len(error_message) < 200:
            base_message += f" 詳細: {error_message}"
        
        return base_message
    
    @staticmethod
    def create_error_context(operation: str, **params) -> Dict[str, Any]:
        """
        エラーコンテキスト情報を作成
        
        Args:
            operation: 実行していた操作名
            **params: パラメータ情報
            
        Returns:
            エラーコンテキスト辞書
        """
        context = {'operation': operation}
        
        # 重要なパラメータのみをコンテキストに含める
        important_params = [
            'memory_id', 'runtime_id', 'event_id', 'actor_id', 'session_id', 'invocation_id'
        ]
        for param in important_params:
            if param in params and params[param] is not None:
                context[param] = params[param]
        
        return context
    
    @staticmethod
    def format_backup_error(error_key: str, **kwargs) -> str:
        """
        バックアップ操作のエラーメッセージをフォーマット
        
        Args:
            error_key: エラーメッセージのキー
            **kwargs: メッセージに埋め込む値
            
        Returns:
            フォーマットされたエラーメッセージ
        """
        if error_key not in AgentCoreError.BACKUP_ERROR_MESSAGES:
            return f'バックアップ操作でエラーが発生しました: {error_key}'
        
        template = AgentCoreError.BACKUP_ERROR_MESSAGES[error_key]
        try:
            return template.format(**kwargs)
        except KeyError as e:
            return f'{template} (パラメータ不足: {e})'
    
    @staticmethod
    def format_restore_error(error_key: str, **kwargs) -> str:
        """
        リストア操作のエラーメッセージをフォーマット
        
        Args:
            error_key: エラーメッセージのキー
            **kwargs: メッセージに埋め込む値
            
        Returns:
            フォーマットされたエラーメッセージ
        """
        if error_key not in AgentCoreError.RESTORE_ERROR_MESSAGES:
            return f'リストア操作でエラーが発生しました: {error_key}'
        
        template = AgentCoreError.RESTORE_ERROR_MESSAGES[error_key]
        try:
            return template.format(**kwargs)
        except KeyError as e:
            return f'{template} (パラメータ不足: {e})'
    
    @staticmethod
    def format_merge_error(error_key: str, **kwargs) -> str:
        """
        マージ操作のエラーメッセージをフォーマット
        
        Args:
            error_key: エラーメッセージのキー
            **kwargs: メッセージに埋め込む値
            
        Returns:
            フォーマットされたエラーメッセージ
        """
        if error_key not in AgentCoreError.MERGE_ERROR_MESSAGES:
            return f'マージ操作でエラーが発生しました: {error_key}'
        
        template = AgentCoreError.MERGE_ERROR_MESSAGES[error_key]
        try:
            return template.format(**kwargs)
        except KeyError as e:
            return f'{template} (パラメータ不足: {e})'
    
    @staticmethod
    def format_split_error(error_key: str, **kwargs) -> str:
        """
        分割操作のエラーメッセージをフォーマット
        
        Args:
            error_key: エラーメッセージのキー
            **kwargs: メッセージに埋め込む値
            
        Returns:
            フォーマットされたエラーメッセージ
        """
        if error_key not in AgentCoreError.SPLIT_ERROR_MESSAGES:
            return f'分割操作でエラーが発生しました: {error_key}'
        
        template = AgentCoreError.SPLIT_ERROR_MESSAGES[error_key]
        try:
            return template.format(**kwargs)
        except KeyError as e:
            return f'{template} (パラメータ不足: {e})'
    
    @staticmethod
    def format_copy_error(error_key: str, **kwargs) -> str:
        """
        コピー操作のエラーメッセージをフォーマット
        
        Args:
            error_key: エラーメッセージのキー
            **kwargs: メッセージに埋め込む値
            
        Returns:
            フォーマットされたエラーメッセージ
        """
        if error_key not in AgentCoreError.COPY_ERROR_MESSAGES:
            return f'コピー操作でエラーが発生しました: {error_key}'
        
        template = AgentCoreError.COPY_ERROR_MESSAGES[error_key]
        try:
            return template.format(**kwargs)
        except KeyError as e:
            return f'{template} (パラメータ不足: {e})'