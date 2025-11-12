"""
場所: tools/bedrock_kb_list_data_sources.py
内容: 指定した Amazon Bedrock ナレッジベースに紐づくデータソース一覧を取得するツール。
目的: Knowledge Base ID から関連データソースの状態やコネクタ設定を確認し、同期ツールへの引き継ぎを容易にする。
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

try:
    from my_aws_tools.provider.utils import (
        build_boto3_client_kwargs,
        resolve_aws_credentials,
        reset_clients_on_credential_change,
    )
except ModuleNotFoundError:  # pragma: no cover
    from provider.utils import (
        build_boto3_client_kwargs,
        resolve_aws_credentials,
        reset_clients_on_credential_change,
    )


class BedrockKBListDataSourcesTool(Tool):
    bedrock_client: Any | None = None

    def _ensure_client(self, credentials: dict[str, Any]) -> None:
        reset_clients_on_credential_change(self, credentials, ["bedrock_client"])
        if not self.bedrock_client:
            client_kwargs = build_boto3_client_kwargs(credentials)
            self.bedrock_client = boto3.client("bedrock-agent", **client_kwargs)

    @staticmethod
    def _serialize_summary(summary: dict[str, Any]) -> dict[str, Any]:
        serialized: dict[str, Any] = {}
        for key, value in summary.items():
            if hasattr(value, "isoformat"):
                serialized[key] = value.isoformat()
            else:
                serialized[key] = value
        return serialized

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            if tool_parameters.get("aws_region"):
                credentials["aws_region"] = tool_parameters["aws_region"]
            self._ensure_client(credentials)
        except Exception as exc:  # pragma: no cover
            yield self.create_text_message(f"Failed to initialize AWS client: {exc}")
            return

        knowledge_base_id = tool_parameters.get("knowledge_base_id")
        if not knowledge_base_id:
            yield self.create_text_message("knowledge_base_id parameter is required")
            return

        max_results = tool_parameters.get("max_results")
        next_token = tool_parameters.get("next_token")

        request_kwargs: dict[str, Any] = {"knowledgeBaseId": knowledge_base_id}
        if max_results:
            try:
                request_kwargs["maxResults"] = int(max_results)
            except (TypeError, ValueError):
                yield self.create_text_message("max_results must be an integer")
                return
        if next_token:
            request_kwargs["nextToken"] = next_token

        try:
            response = self.bedrock_client.list_data_sources(**request_kwargs)
        except (BotoCoreError, ClientError) as exc:
            message = getattr(exc, "response", {}).get("Error", {}).get("Message", str(exc))
            yield self.create_text_message(f"Failed to list data sources: {message}")
            return

        summaries = response.get("dataSourceSummaries", []) or []
        serialized = [self._serialize_summary(summary) for summary in summaries]
        result_payload = {
            "knowledge_base_id": knowledge_base_id,
            "data_sources": serialized,
            "next_token": response.get("nextToken"),
        }

        yield self.create_json_message(result_payload)

        if serialized:
            def _get_field(summary: dict[str, Any], key: str) -> str:
                value = summary.get(key, "")
                return str(value) if value is not None else ""

            text_lines = [
                ",".join(
                    [
                        _get_field(summary, "dataSourceId"),
                        _get_field(summary, "name"),
                        _get_field(summary, "status"),
                    ]
                )
                for summary in serialized
            ]
            yield self.create_text_message("\n".join(text_lines))
        else:
            yield self.create_text_message(f"No data sources found for {knowledge_base_id}")
