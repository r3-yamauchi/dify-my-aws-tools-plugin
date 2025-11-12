import boto3
import json
from collections.abc import Iterable
from botocore.exceptions import ClientError
from typing import Optional, Dict, Any, Union, Tuple


class ParameterStoreManager:
    """AWS Parameter Store utility class for read/write operations with dict support"""
    
    def __init__(
        self,
        region_name: str = 'us-east-1',
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
    ):
        client_kwargs: Dict[str, Any] = {'region_name': region_name}
        if aws_access_key_id and aws_secret_access_key:
            client_kwargs['aws_access_key_id'] = aws_access_key_id
            client_kwargs['aws_secret_access_key'] = aws_secret_access_key
        self.ssm_client = boto3.client('ssm', **client_kwargs)
    
    def get_parameter(self, name: str, decrypt: bool = True, as_dict: bool = False) -> Optional[Union[str, Dict]]:
        """
        Get parameter value from Parameter Store
        
        Args:
            name: Parameter name
            decrypt: Whether to decrypt SecureString parameters
            as_dict: Whether to parse JSON string as dict
            
        Returns:
            Parameter value (string or dict) or None if not found
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
                    return value
            return value
        except ClientError as e:
            if e.response['Error']['Code'] == 'ParameterNotFound':
                return None
            raise e
    
    def put_parameter(self, name: str, value: Union[str, Dict, Any], parameter_type: str = 'String', 
                     overwrite: bool = True, description: str = '') -> bool:
        """
        Put parameter to Parameter Store (supports dict objects)
        
        Args:
            name: Parameter name
            value: Parameter value (string, dict, or any JSON-serializable object)
            parameter_type: String, StringList, or SecureString
            overwrite: Whether to overwrite existing parameter
            description: Parameter description
            
        Returns:
            True if successful
        """
        try:
            # Convert dict/object to JSON string
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
        """Delete parameter from Parameter Store"""
        try:
            self.ssm_client.delete_parameter(Name=name)
            return True
        except ClientError:
            return False


CredentialSignature = Tuple[Optional[str], Optional[str], Optional[str]]


def resolve_aws_credentials(tool: Any, tool_parameters: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Merge provider-level credentials with tool parameters, preferring tool-specific inputs."""
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
    """Construct boto3 client kwargs from merged credentials."""
    kwargs: Dict[str, Any] = {}
    if credentials.get('aws_region'):
        kwargs['region_name'] = credentials['aws_region']
    if credentials.get('aws_access_key_id') and credentials.get('aws_secret_access_key'):
        kwargs['aws_access_key_id'] = credentials['aws_access_key_id']
        kwargs['aws_secret_access_key'] = credentials['aws_secret_access_key']
    return kwargs


def build_credential_signature(credentials: Dict[str, Optional[str]]) -> CredentialSignature:
    """Return tuple identifying credentials for cache invalidation."""

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
    """Reset cached boto3 clients/resources when AK/SK/region changed."""

    signature = build_credential_signature(credentials)
    current_signature = getattr(owner, signature_attr, None)
    if current_signature != signature:
        for attr in client_attrs:
            if hasattr(owner, attr):
                setattr(owner, attr, None)
        setattr(owner, signature_attr, signature)
