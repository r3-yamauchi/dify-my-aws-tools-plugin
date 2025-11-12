"""
場所: tools/lambda_yaml_to_json.py
内容: 任意の AWS Lambda 関数を呼び出して YAML を JSON へ変換するツール。
目的: Workflow から YAML テキストを渡し、サーバーレスに検証・変換した結果を取得できるようにする。
"""

import json
import logging
from typing import Any, Union
from collections.abc import Generator

import boto3  # type: ignore

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from provider.utils import (
    resolve_aws_credentials,
    build_boto3_client_kwargs,
    reset_clients_on_credential_change,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

console_handler = logging.StreamHandler()
logger.addHandler(console_handler)


class LambdaYamlToJsonTool(Tool):
    lambda_client: Any = None

    def _invoke_lambda(self, lambda_name: str, yaml_content: str) -> str:
        msg = {"body": yaml_content}
        logger.info(json.dumps(msg))

        invoke_response = self.lambda_client.invoke(
            FunctionName=lambda_name, InvocationType="RequestResponse", Payload=json.dumps(msg)
        )
        response_body = invoke_response["Payload"]

        response_str = response_body.read().decode("utf-8")
        resp_json = json.loads(response_str)

        logger.info(resp_json)
        if resp_json["statusCode"] != 200:
            raise Exception(f"Invalid status code: {response_str}")

        return resp_json["body"]

    def _invoke(
        self,
        tool_parameters: dict[str, Any],
    ) -> Generator[ToolInvokeMessage]:
        """YAML と Lambda 情報を検証し、変換結果をテキストで返す."""
        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            if tool_parameters.get("aws_region"):
                credentials["aws_region"] = tool_parameters.get("aws_region")

            reset_clients_on_credential_change(self, credentials, ["lambda_client"])

            if not self.lambda_client:
                client_kwargs = build_boto3_client_kwargs(credentials)
                self.lambda_client = boto3.client("lambda", **client_kwargs)

            yaml_content = tool_parameters.get("yaml_content", "")
            if not yaml_content:
                return self.create_text_message("Please input yaml_content")

            lambda_name = tool_parameters.get("lambda_name", "")
            if not lambda_name:
                return self.create_text_message("Please input lambda_name")
            logger.debug(f"{json.dumps(tool_parameters, indent=2, ensure_ascii=False)}")

            result = self._invoke_lambda(lambda_name, yaml_content)
            logger.debug(result)

            return self.create_text_message(result)
        except Exception as e:
            return self.create_text_message(f"Exception: {str(e)}")

        console_handler.flush()
