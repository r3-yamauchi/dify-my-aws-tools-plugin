"""
場所: tools/lambda_invoker.py
内容: 任意の AWS Lambda 関数へ JSON ペイロードを同期/非同期で送信し、結果やログを取得するツール。
目的: Dify から既存のサーバーレス関数を安全に再利用し、追加コードなしでワークフローへ組み込めるようにする。
"""

from __future__ import annotations

import base64
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


class LambdaInvokerTool(Tool):
    lambda_client: Any | None = None

    def _ensure_client(self, credentials: dict[str, Any]) -> None:
        reset_clients_on_credential_change(self, credentials, ["lambda_client"])
        if not self.lambda_client:
            client_kwargs = build_boto3_client_kwargs(credentials)
            self.lambda_client = boto3.client("lambda", **client_kwargs)

    def _load_json(
        self,
        value: Any,
        param_name: str,
        default: Any,
    ) -> tuple[Any, str | None]:
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

        lambda_name = tool_parameters.get("lambda_name")
        if not lambda_name:
            yield self.create_text_message("lambda_name parameter is required")
            return

        payload_obj, payload_error = self._load_json(tool_parameters.get("payload_json"), "payload_json", {})
        if payload_error:
            yield self.create_text_message(payload_error)
            return
        if payload_obj is None:
            payload_obj = {}

        client_context, context_error = self._load_json(
            tool_parameters.get("client_context_json"),
            "client_context_json",
            None,
        )
        if context_error:
            yield self.create_text_message(context_error)
            return

        invocation_type = tool_parameters.get("invocation_type", "RequestResponse")
        qualifier = tool_parameters.get("qualifier")
        include_logs = bool(tool_parameters.get("include_logs"))

        invoke_kwargs: dict[str, Any] = {
            "FunctionName": lambda_name,
            "InvocationType": invocation_type,
            "Payload": json.dumps(payload_obj or {}, ensure_ascii=False).encode("utf-8"),
        }
        if qualifier:
            invoke_kwargs["Qualifier"] = qualifier
        if include_logs:
            invoke_kwargs["LogType"] = "Tail"
        if client_context:
            encoded_context = base64.b64encode(json.dumps(client_context, ensure_ascii=False).encode("utf-8")).decode("utf-8")
            invoke_kwargs["ClientContext"] = encoded_context

        try:
            response = self.lambda_client.invoke(**invoke_kwargs)
        except (BotoCoreError, ClientError) as exc:
            message = getattr(exc, "response", {}).get("Error", {}).get("Message", str(exc))
            yield self.create_text_message(f"Failed to invoke Lambda: {message}")
            return

        payload_stream = response.get("Payload")
        response_text = payload_stream.read().decode("utf-8") if payload_stream else ""
        response_json: Any | None = None
        if response_text:
            try:
                response_json = json.loads(response_text)
            except json.JSONDecodeError:
                response_json = None

        result_payload: dict[str, Any] = {
            "function_name": lambda_name,
            "status_code": response.get("StatusCode"),
            "executed_version": response.get("ExecutedVersion"),
            "invocation_type": invocation_type,
        }
        if qualifier:
            result_payload["qualifier"] = qualifier
        if response_json is not None:
            result_payload["response_json"] = response_json
        elif response_text:
            result_payload["response_text"] = response_text

        if include_logs and response.get("LogResult"):
            try:
                decoded_logs = base64.b64decode(response["LogResult"]).decode("utf-8", errors="ignore")
                result_payload["logs"] = decoded_logs
            except Exception:  # pragma: no cover - corrupted log only
                result_payload["logs"] = "Failed to decode logs"

        yield self.create_json_message(result_payload)

        if response_json is not None:
            text_output = json.dumps(response_json, ensure_ascii=False)
        elif response_text:
            text_output = response_text
        else:
            text_output = f"Invoked {lambda_name} (status {result_payload['status_code']})"
        yield self.create_text_message(text_output)
