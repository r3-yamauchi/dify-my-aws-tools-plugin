"""
場所: tools/bedrock_kb_start_ingestion_job.py
内容: Amazon Bedrock Knowledge Base のデータソースに対して StartIngestionJob API を実行し、同期ジョブの開始情報を返すツール。
目的: Dify ワークフローからナレッジベースのデータソース同期をオンデマンドでトリガーし、実行 ID やステータスをエージェントに共有する。
"""

from __future__ import annotations

import json
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


class BedrockKBStartIngestionJobTool(Tool):
    bedrock_client: Any | None = None

    def _ensure_client(self, credentials: dict[str, Any]) -> None:
        reset_clients_on_credential_change(self, credentials, ["bedrock_client"])
        if not self.bedrock_client:
            client_kwargs = build_boto3_client_kwargs(credentials)
            self.bedrock_client = boto3.client("bedrock-agent", **client_kwargs)

    @staticmethod
    def _format_datetime(value: Any) -> Any:
        if hasattr(value, "isoformat"):
            return value.isoformat()
        return value

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            if tool_parameters.get("aws_region"):
                credentials["aws_region"] = tool_parameters["aws_region"]
            self._ensure_client(credentials)
        except Exception as exc:  # pragma: no cover - boto3 init failures are rare
            yield self.create_text_message(f"Failed to initialize AWS client: {exc}")
            return

        knowledge_base_id = tool_parameters.get("knowledge_base_id")
        if not knowledge_base_id:
            yield self.create_text_message("knowledge_base_id parameter is required")
            return

        data_source_id = tool_parameters.get("data_source_id")
        if not data_source_id:
            yield self.create_text_message("data_source_id parameter is required")
            return

        client_token = tool_parameters.get("client_token")
        data_deletion_policy = tool_parameters.get("data_deletion_policy")

        start_kwargs: dict[str, Any] = {
            "knowledgeBaseId": knowledge_base_id,
            "dataSourceId": data_source_id,
        }
        if client_token:
            start_kwargs["clientToken"] = client_token
        if data_deletion_policy:
            start_kwargs["dataDeletionPolicy"] = data_deletion_policy

        try:
            response = self.bedrock_client.start_ingestion_job(**start_kwargs)
        except (BotoCoreError, ClientError) as exc:
            message = getattr(exc, "response", {}).get("Error", {}).get("Message", str(exc))
            yield self.create_text_message(f"Failed to start ingestion job: {message}")
            return

        ingestion_job: dict[str, Any] = response.get("ingestionJob", {}) or {}
        serialized_job = {
            key: self._format_datetime(value)
            for key, value in ingestion_job.items()
        }

        result_payload = {
            "knowledge_base_id": knowledge_base_id,
            "data_source_id": data_source_id,
            "ingestion_job": serialized_job,
        }

        yield self.create_json_message(result_payload)

        job_id = serialized_job.get("ingestionJobId") or serialized_job.get("ingestion_job_id")
        status = serialized_job.get("status")
        summary = f"Started ingestion job {job_id or ''}".strip()
        if status:
            summary += f" (status {status})"
        yield self.create_text_message(summary)
