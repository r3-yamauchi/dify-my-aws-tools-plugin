"""
場所: tools/sts_assume_role.py
内容: AWS STS AssumeRole を実行して一時的な認証情報を取得するツール。
目的: IAM ロールを引き受けて一時的な認証情報を取得し、他のツールで使用できる形式で返却する。
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

try:
    from utils.utils import (
        build_boto3_client_kwargs,
        resolve_aws_credentials,
        reset_clients_on_credential_change,
    )
except ModuleNotFoundError:  # pragma: no cover
    from utils.utils import (
        build_boto3_client_kwargs,
        resolve_aws_credentials,
        reset_clients_on_credential_change,
    )


class STSAssumeRole(Tool):
    """AWS STS AssumeRole を実行して一時的な認証情報を取得するツール。"""

    sts_client: Any | None = None

    def _ensure_client(self, credentials: dict[str, Any]) -> None:
        """STS クライアントを初期化または再利用する。"""
        reset_clients_on_credential_change(self, credentials, ["sts_client"])
        if not self.sts_client:
            client_kwargs = build_boto3_client_kwargs(credentials)
            self.sts_client = boto3.client("sts", **client_kwargs)

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        指定されたロールを引き受けて一時的な認証情報を取得し、
        get_credentials ツールと互換性のある形式で返却する。
        """

        # 必須パラメータの検証（クライアント初期化の前に実行）
        role_arn = tool_parameters.get("role_arn")
        if not role_arn:
            yield self.create_text_message("role_arn パラメータは必須です")
            return

        # AWS 認証情報を解決（プロバイダーレベルとツールパラメータをマージ）
        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            if tool_parameters.get("aws_region"):
                credentials["aws_region"] = tool_parameters["aws_region"]
            self._ensure_client(credentials)
        except Exception as exc:  # pragma: no cover - boto3 init failures
            yield self.create_text_message(f"AWS クライアントの初期化に失敗しました: {exc}")
            return

        # RoleSessionName のデフォルト値を設定
        role_session_name = tool_parameters.get("role_session_name", "DifySession")

        # AssumeRole のパラメータを構築
        assume_role_kwargs: dict[str, Any] = {
            "RoleArn": role_arn,
            "RoleSessionName": role_session_name,
        }

        # オプションパラメータを追加
        if tool_parameters.get("duration_seconds"):
            assume_role_kwargs["DurationSeconds"] = int(tool_parameters["duration_seconds"])

        if tool_parameters.get("external_id"):
            assume_role_kwargs["ExternalId"] = tool_parameters["external_id"]

        if tool_parameters.get("serial_number"):
            assume_role_kwargs["SerialNumber"] = tool_parameters["serial_number"]

        if tool_parameters.get("token_code"):
            assume_role_kwargs["TokenCode"] = tool_parameters["token_code"]

        if tool_parameters.get("policy"):
            assume_role_kwargs["Policy"] = tool_parameters["policy"]

        # AssumeRole を実行
        try:
            response = self.sts_client.assume_role(**assume_role_kwargs)
        except (BotoCoreError, ClientError) as exc:
            error_message = getattr(exc, "response", {}).get("Error", {}).get("Message", str(exc))
            yield self.create_text_message(f"AssumeRole の実行に失敗しました: {error_message}")
            return
        except Exception as exc:  # pragma: no cover - unexpected errors
            yield self.create_text_message(f"予期しないエラーが発生しました: {exc}")
            return

        # レスポンスから認証情報を取得
        credentials_data = response.get("Credentials", {})
        assumed_role_user = response.get("AssumedRoleUser", {})

        # get_credentials ツールと互換性のある形式で認証情報を構築
        credentials_dict = {
            "access_key": credentials_data.get("AccessKeyId"),
            "secret_key": credentials_data.get("SecretAccessKey"),
            "token": credentials_data.get("SessionToken"),
        }

        # セッション情報を追加
        session_info = {
            "role_arn": role_arn,
            "role_session_name": role_session_name,
            "assumed_role_id": assumed_role_user.get("AssumedRoleId"),
            "assumed_role_arn": assumed_role_user.get("Arn"),
            "expiration": credentials_data.get("Expiration").isoformat() if credentials_data.get("Expiration") else None,
        }

        # 最終的なペイロードを構築
        payload = {
            "credentials": credentials_dict,
            "session_info": session_info,
        }

        # JSON メッセージとして返却
        yield self.create_json_message(payload)

        # テキストサマリーも返却
        summary_lines = [
            f"ロール ARN: {role_arn}",
            f"セッション名: {role_session_name}",
            f"アクセスキー: {credentials_dict['access_key'][:10]}...",
            f"有効期限: {session_info['expiration']}",
        ]
        yield self.create_text_message("\n".join(summary_lines))
