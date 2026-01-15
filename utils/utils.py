import boto3
import json
from collections.abc import Iterable
from botocore.exceptions import ClientError
from typing import Optional, Dict, Any, Union, Tuple


class ParameterStoreManager:
    """
    AWS Parameter Store ユーティリティクラス
    
    辞書型データの読み書きをサポートする Parameter Store 操作クラス。
    JSON シリアライゼーションを自動で処理し、暗号化パラメータにも対応する。
    """
    
    def __init__(
        self,
        region_name: str = 'us-east-1',
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
    ):
        """
        Parameter Store マネージャーを初期化する
        
        Args:
            region_name: AWS リージョン名（デフォルト: us-east-1）
            aws_access_key_id: AWS アクセスキー ID（オプション）
            aws_secret_access_key: AWS シークレットアクセスキー（オプション）
        """
        client_kwargs: Dict[str, Any] = {'region_name': region_name}
        if aws_access_key_id and aws_secret_access_key:
            client_kwargs['aws_access_key_id'] = aws_access_key_id
            client_kwargs['aws_secret_access_key'] = aws_secret_access_key
        self.ssm_client = boto3.client('ssm', **client_kwargs)
    
    def get_parameter(self, name: str, decrypt: bool = True, as_dict: bool = False) -> Optional[Union[str, Dict]]:
        """
        Parameter Store からパラメータ値を取得する
        
        Args:
            name: パラメータ名
            decrypt: SecureString パラメータを復号化するかどうか
            as_dict: JSON 文字列を辞書として解析するかどうか
            
        Returns:
            パラメータ値（文字列または辞書）、見つからない場合は None
        """
        try:
            response = self.ssm_client.get_parameter(
                Name=name,
                WithDecryption=decrypt
            )
            value = response['Parameter']['Value']
            
            if as_dict:
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    # JSON として解析できない場合は文字列のまま返す
                    return value
            return value
        except ClientError as e:
            if e.response['Error']['Code'] == 'ParameterNotFound':
                return None
            raise e
    
    def put_parameter(self, name: str, value: Union[str, Dict, Any], parameter_type: str = 'String', 
                     overwrite: bool = True, description: str = '') -> bool:
        """
        Parameter Store にパラメータを保存する（辞書オブジェクトをサポート）
        
        Args:
            name: パラメータ名
            value: パラメータ値（文字列、辞書、または JSON シリアライズ可能なオブジェクト）
            parameter_type: パラメータタイプ（String、StringList、SecureString）
            overwrite: 既存パラメータを上書きするかどうか
            description: パラメータの説明
            
        Returns:
            成功した場合は True
        """
        try:
            # 辞書やオブジェクトを JSON 文字列に変換
            if isinstance(value, (dict, list)) or not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False)
            
            self.ssm_client.put_parameter(
                Name=name,
                Value=value,
                Type=parameter_type,
                Overwrite=overwrite,
                Description=description
            )
            return True
        except (ClientError, json.JSONEncodeError):
            return False
    
    def delete_parameter(self, name: str) -> bool:
        """
        Parameter Store からパラメータを削除する
        
        Args:
            name: 削除するパラメータ名
            
        Returns:
            成功した場合は True
        """
        try:
            self.ssm_client.delete_parameter(Name=name)
            return True
        except ClientError:
            return False


# 認証情報の署名を表すタプル型（キャッシュ無効化用）
CredentialSignature = Tuple[Optional[str], Optional[str], Optional[str]]


def resolve_aws_credentials(tool: Any, tool_parameters: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """
    プロバイダーレベルの認証情報とツールパラメータをマージし、ツール固有の入力を優先する
    
    Args:
        tool: ツールインスタンス
        tool_parameters: ツールパラメータ辞書
        
    Returns:
        マージされた認証情報辞書
    """
    runtime_credentials = getattr(getattr(tool, 'runtime', None), 'credentials', {}) or {}

    aws_access_key_id = tool_parameters.get('aws_access_key_id') or runtime_credentials.get('aws_access_key_id')
    aws_secret_access_key = tool_parameters.get('aws_secret_access_key') or runtime_credentials.get('aws_secret_access_key')
    aws_region = tool_parameters.get('aws_region') or runtime_credentials.get('aws_region') or 'us-east-1'

    return {
        'aws_access_key_id': aws_access_key_id,
        'aws_secret_access_key': aws_secret_access_key,
        'aws_region': aws_region,
    }


def build_boto3_client_kwargs(credentials: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """
    マージされた認証情報から boto3 クライアント引数を構築する
    
    Args:
        credentials: 認証情報辞書
        
    Returns:
        boto3 クライアント初期化用の引数辞書
    """
    kwargs: Dict[str, Any] = {}
    if credentials.get('aws_region'):
        kwargs['region_name'] = credentials['aws_region']
    if credentials.get('aws_access_key_id') and credentials.get('aws_secret_access_key'):
        kwargs['aws_access_key_id'] = credentials['aws_access_key_id']
        kwargs['aws_secret_access_key'] = credentials['aws_secret_access_key']
    return kwargs


def build_credential_signature(credentials: Dict[str, Optional[str]]) -> CredentialSignature:
    """
    キャッシュ無効化用の認証情報識別タプルを返す
    
    Args:
        credentials: 認証情報辞書
        
    Returns:
        認証情報を識別するタプル（アクセスキー、シークレットキー、リージョン）
    """
    return (
        credentials.get('aws_access_key_id'),
        credentials.get('aws_secret_access_key'),
        credentials.get('aws_region'),
    )


def reset_clients_on_credential_change(
    owner: Any,
    credentials: Dict[str, Optional[str]],
    client_attrs: Iterable[str],
    signature_attr: str = '_client_credentials_signature',
) -> None:
    """
    AK/SK/リージョンが変更された際にキャッシュされた boto3 クライアント/リソースをリセットする
    
    Args:
        owner: クライアントを保持するオブジェクト
        credentials: 現在の認証情報
        client_attrs: リセット対象のクライアント属性名のリスト
        signature_attr: 認証情報署名を保存する属性名
    """
    signature = build_credential_signature(credentials)
    current_signature = getattr(owner, signature_attr, None)
    if current_signature != signature:
        # 認証情報が変更された場合、すべてのクライアントをリセット
        for attr in client_attrs:
            if hasattr(owner, attr):
                setattr(owner, attr, None)
        setattr(owner, signature_attr, signature)
