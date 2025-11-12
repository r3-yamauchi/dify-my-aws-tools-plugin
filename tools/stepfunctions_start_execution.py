"""
場所: tools/stepfunctions_start_execution.py
内容: AWS Step Functions のステートマシンを Dify から直接起動し、実行 ARN や開始時刻を返すツール。
目的: 既存のワークフロー定義を追加のサーバーコードなしで起動し、エージェントの分岐ロジックに組み込めるようにする。
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


class StepFunctionsStartExecutionTool(Tool):
    stepfunctions_client: Any | None = None

    def _ensure_client(self, credentials: dict[str, Any]) -> None:
        reset_clients_on_credential_change(self, credentials, ["stepfunctions_client"])
        if not self.stepfunctions_client:
            client_kwargs = build_boto3_client_kwargs(credentials)
            self.stepfunctions_client = boto3.client("stepfunctions", **client_kwargs)

    def _parse_json_input(self, value: Any, param_name: str, default: Any) -> tuple[Any, str | None]:
        if value in (None, ""):
            return default, None
        if isinstance(value, (dict, list)):
            try:
                json.dumps(value)
            except (TypeError, ValueError) as exc:
                return None, f"{param_name} must be JSON serializable: {exc}"
            return value, None
        if not isinstance(value, str):
            return None, f"{param_name} must be a JSON string or object"
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            return None, f"{param_name} must be valid JSON: {exc}"
        return parsed, None

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            if tool_parameters.get("aws_region"):
                credentials["aws_region"] = tool_parameters["aws_region"]
            self._ensure_client(credentials)
        except Exception as exc:  # pragma: no cover - boto3 init failures are rare
            yield self.create_text_message(f"Failed to initialize AWS client: {exc}")
            return

        state_machine_arn = tool_parameters.get("state_machine_arn")
        if not state_machine_arn:
            yield self.create_text_message("state_machine_arn parameter is required")
            return

        input_payload, payload_error = self._parse_json_input(tool_parameters.get("input_json"), "input_json", {})
        if payload_error:
            yield self.create_text_message(payload_error)
            return
        if input_payload is None:
            input_payload = {}

        tags, tags_error = self._parse_json_input(tool_parameters.get("tags_json"), "tags_json", None)
        if tags_error:
            yield self.create_text_message(tags_error)
            return

        execution_name = tool_parameters.get("execution_name")
        trace_header = tool_parameters.get("trace_header")

        start_kwargs: dict[str, Any] = {
            "stateMachineArn": state_machine_arn,
            "input": json.dumps(input_payload, ensure_ascii=False),
        }
        if execution_name:
            start_kwargs["name"] = execution_name
        if trace_header:
            start_kwargs["traceHeader"] = trace_header
        if tags:
            if not isinstance(tags, list):
                yield self.create_text_message("tags_json must be a JSON array of {\"key\":..., \"value\":...}")
                return
            start_kwargs["tags"] = tags

        try:
            response = self.stepfunctions_client.start_execution(**start_kwargs)
        except (BotoCoreError, ClientError) as exc:
            message = getattr(exc, "response", {}).get("Error", {}).get("Message", str(exc))
            yield self.create_text_message(f"Failed to start execution: {message}")
            return

        start_date = response.get("startDate")
        if hasattr(start_date, "isoformat"):
            start_date_str = start_date.isoformat()
        elif start_date is not None:
            start_date_str = str(start_date)
        else:
            start_date_str = None

        result_payload: dict[str, Any] = {
            "state_machine_arn": state_machine_arn,
            "execution_arn": response.get("executionArn"),
            "start_date": start_date_str,
        }
        if execution_name:
            result_payload["execution_name"] = execution_name
        if trace_header:
            result_payload["trace_header"] = trace_header
        if tags:
            result_payload["tags"] = tags

        yield self.create_json_message(result_payload)
        yield self.create_text_message(result_payload["execution_arn"] or "Execution started")
