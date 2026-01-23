"""
場所: tools/get_credentials.py
内容: boto3.Session から AWS 認証情報を取得し、JSON 形式で返却するツール。
目的: 指定したプロファイルの認証情報を取得し、他のツールで使用できるようにする。
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError, ProfileNotFound

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage


class GetCredentials(Tool):
    """boto3.Session から AWS 認証情報を取得するツール。"""

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """
        指定されたプロファイルとリージョンで boto3.Session を作成し、
        認証情報を取得して JSON 形式で返却する。
        """

        profile_name = tool_parameters.get("profile_name")
        region_name = tool_parameters.get("region_name")

        # boto3.Session のパラメータを構築
        session_kwargs: dict[str, Any] = {}
        if profile_name:
            session_kwargs["profile_name"] = profile_name
        if region_name:
            session_kwargs["region_name"] = region_name

        try:
            # boto3.Session を作成
            session = boto3.Session(**session_kwargs)

            # 認証情報を取得
            credentials = session.get_credentials()

            if credentials is None:
                yield self.create_text_message(
                    "認証情報を取得できませんでした。プロファイル名またはデフォルト認証情報を確認してください。"
                )
                return

            # 認証情報を辞書形式で構築
            credentials_dict = {
                "access_key": credentials.access_key,
                "secret_key": credentials.secret_key,
                "token": credentials.token,  # セッショントークン（存在する場合）
            }

            # セッション情報も追加
            session_info = {
                "profile_name": profile_name or "default",
                "region_name": session.region_name or "not specified",
                "available_profiles": session.available_profiles,
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
                f"プロファイル: {session_info['profile_name']}",
                f"リージョン: {session_info['region_name']}",
                f"アクセスキー: {credentials_dict['access_key'][:10]}...",
                f"セッショントークン: {'あり' if credentials_dict['token'] else 'なし'}",
            ]
            yield self.create_text_message("\n".join(summary_lines))

        except ProfileNotFound as exc:
            yield self.create_text_message(f"指定されたプロファイルが見つかりません: {exc}")
            return

        except (BotoCoreError, ClientError) as exc:
            yield self.create_text_message(f"AWS 認証情報の取得に失敗しました: {exc}")
            return

        except Exception as exc:  # pragma: no cover - unexpected errors
            yield self.create_text_message(f"予期しないエラーが発生しました: {exc}")
            return
