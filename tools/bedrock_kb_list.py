"""
場所: tools/bedrock_kb_list.py
内容: Amazon Bedrock Knowledge Base の一覧を取得し、各サマリー情報を返すツール。
目的: Dify からナレッジベースの存在・状態を即座に確認し、ID を後続ツールへ受け渡す用途をカバーする。
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


class BedrockKBListTool(Tool):
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

        max_results = tool_parameters.get("max_results")
        next_token = tool_parameters.get("next_token")

        request_kwargs: dict[str, Any] = {}
        if max_results:
            try:
                request_kwargs["maxResults"] = int(max_results)
            except (TypeError, ValueError):
                yield self.create_text_message("max_results must be an integer")
                return
        if next_token:
            request_kwargs["nextToken"] = next_token

        try:
            response = self.bedrock_client.list_knowledge_bases(**request_kwargs)
        except (BotoCoreError, ClientError) as exc:
            message = getattr(exc, "response", {}).get("Error", {}).get("Message", str(exc))
            yield self.create_text_message(f"Failed to list knowledge bases: {message}")
            return

        summaries = response.get("knowledgeBaseSummaries", []) or []
        serialized_summaries = [self._serialize_summary(summary) for summary in summaries]
        result_payload = {
            "knowledge_bases": serialized_summaries,
            "next_token": response.get("nextToken"),
        }

        yield self.create_json_message(result_payload)

        if serialized_summaries:
            def _get_field(summary: dict[str, Any], key: str) -> str:
                value = summary.get(key, "")
                return str(value) if value is not None else ""

            text_lines = [
                ",".join(
                    [
                        _get_field(summary, "knowledgeBaseId"),
                        _get_field(summary, "name"),
                        _get_field(summary, "status"),
                    ]
                )
                for summary in serialized_summaries
            ]
            yield self.create_text_message("\n".join(text_lines))
        else:
            yield self.create_text_message("No knowledge bases found")
