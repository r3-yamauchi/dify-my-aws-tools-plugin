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