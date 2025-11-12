"""
場所: tools/lambda_translate_utils.py
内容: AWS Lambda で実装された翻訳ユーティリティを呼び出し、用語辞書やモデル ID を柔軟に指定して翻訳するツール。
目的: Dify からサーバーレスな翻訳パイプラインへテキストとパラメータを渡し、結果をそのまま取得できるようにする。
"""

import json
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

class LambdaTranslateUtilsTool(Tool):
    lambda_client: Any = None

    def _invoke_lambda(self, text_content, src_lang, dest_lang, model_id, dictionary_name, request_type, lambda_name):
        msg = {
            "src_contents": [text_content],
            "src_lang": src_lang,
            "dest_lang": dest_lang,
            "dictionary_id": dictionary_name,
            "request_type": request_type,
            "model_id": model_id,
        }

        invoke_response = self.lambda_client.invoke(
            FunctionName=lambda_name, InvocationType="RequestResponse", Payload=json.dumps(msg)
        )
        response_body = invoke_response["Payload"]

        response_str = response_body.read().decode("unicode_escape")  # Lambda 側の結果をそのまま返す

        return response_str

    def _invoke(
        self,
        tool_parameters: dict[str, Any],
    ) -> Generator[ToolInvokeMessage]:
        """Lambda 呼び出し前に必須パラメータをチェックし、翻訳結果をテキストで返す."""
        line = 0
        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            if tool_parameters.get("aws_region"):
                credentials["aws_region"] = tool_parameters.get("aws_region")
            reset_clients_on_credential_change(self, credentials, ["lambda_client"])

            if not self.lambda_client:
                client_kwargs = build_boto3_client_kwargs(credentials)
                self.lambda_client = boto3.client("lambda", **client_kwargs)

            line = 1
            text_content = tool_parameters.get("text_content", "")
            if not text_content:
                yield self.create_text_message("Please input text_content")

            line = 2
            src_lang = tool_parameters.get("src_lang", "")
            if not src_lang:
                yield self.create_text_message("Please input src_lang")

            line = 3
            dest_lang = tool_parameters.get("dest_lang", "")
            if not dest_lang:
                yield self.create_text_message("Please input dest_lang")

            line = 4
            lambda_name = tool_parameters.get("lambda_name", "")
            if not lambda_name:
                yield self.create_text_message("Please input lambda_name")

            line = 5
            request_type = tool_parameters.get("request_type", "")
            if not request_type:
                yield self.create_text_message("Please input request_type")

            line = 6
            model_id = tool_parameters.get("model_id", "")
            if not model_id:
                yield self.create_text_message("Please input model_id")

            line = 7
            dictionary_name = tool_parameters.get("dictionary_name", "")
            if not dictionary_name:
                yield self.create_text_message("Please input dictionary_name")

            result = self._invoke_lambda(
                text_content, src_lang, dest_lang, model_id, dictionary_name, request_type, lambda_name
            )

            yield self.create_text_message(text=result)

        except Exception as e:
            yield self.create_text_message(f"Exception {str(e)}, line : {line}")
