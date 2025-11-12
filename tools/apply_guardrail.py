"""
場所: tools/apply_guardrail.py
内容: Amazon Bedrock Guardrails の ApplyGuardrail API を呼び出してコンテンツ安全性をチェックするツール。
目的: Workflow から追加コードを書かずに Bedrock Guardrail を適用し、違反ポリシーやアクション内容を取得できるようにする。
"""

import json
import logging
from typing import Any, Union, Optional
from collections.abc import Generator
from pydantic import BaseModel, Field

from botocore.exceptions import BotoCoreError  # type: ignore
import boto3  # type: ignore

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from provider.utils import resolve_aws_credentials, build_boto3_client_kwargs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class GuardrailParameters(BaseModel):
    guardrail_id: str = Field(..., description="The identifier of the guardrail")
    guardrail_version: str = Field(..., description="The version of the guardrail")
    source: str = Field(..., description="The source of the content")
    text: str = Field(..., description="The text to apply the guardrail to")
    aws_region: Optional[str] = Field(None, description="AWS region for the Bedrock client")


class ApplyGuardrailTool(Tool):
    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage]:
        """ApplyGuardrail API を呼び出し、レスポンスを整形して返却するメインロジック."""
        try:
            # 👉 Pydantic で入力値を検証しつつアクセスを容易にする
            params = GuardrailParameters(**tool_parameters)

            credentials = resolve_aws_credentials(self, tool_parameters)
            client_kwargs = build_boto3_client_kwargs(credentials)
            if params.aws_region:
                client_kwargs["region_name"] = params.aws_region

            # 👉 ガードレール適用は bedrock-runtime クライアントを使う
            bedrock_client = boto3.client("bedrock-runtime", **client_kwargs)

            # 👉 Guardrail API を実行
            response = bedrock_client.apply_guardrail(
                guardrailIdentifier=params.guardrail_id,
                guardrailVersion=params.guardrail_version,
                source=params.source,
                content=[{"text": {"text": params.text}}],
            )

            logger.info(f"Raw response from AWS: {json.dumps(response, indent=2)}")

            # 👉 応答が空ならユーザーに知らせる
            if not response:
                yield self.create_text_message(text="Received empty response from AWS Bedrock.")

            # 👉 代表的なフィールドを取り出して人が読めるテキストに整形
            action = response.get("action", "No action specified")
            outputs = response.get("outputs", [])
            output = outputs[0].get("text", "No output received") if outputs else "No output received"
            assessments = response.get("assessments", [])

            # 👉 ポリシー別の評価内容を単純な文字列へ展開
            formatted_assessments = []
            for assessment in assessments:
                for policy_type, policy_data in assessment.items():
                    if isinstance(policy_data, dict) and "topics" in policy_data:
                        for topic in policy_data["topics"]:
                            formatted_assessments.append(
                                f"Policy: {policy_type}, Topic: {topic['name']}, Type: {topic['type']},"
                                f" Action: {topic['action']}"
                            )
                    else:
                        formatted_assessments.append(f"Policy: {policy_type}, Data: {policy_data}")

            result = f"Action: {action}\n "
            result += f"Output: {output}\n "
            if formatted_assessments:
                result += "Assessments:\n " + "\n ".join(formatted_assessments) + "\n "
            #           result += f"Full response: {json.dumps(response, indent=2, ensure_ascii=False)}"

            yield self.create_text_message(text=result)

        except BotoCoreError as e:
            error_message = f"AWS service error: {str(e)}"
            logger.error(error_message, exc_info=True)
            yield self.create_text_message(text=error_message)
        except json.JSONDecodeError as e:
            error_message = f"JSON parsing error: {str(e)}"
            logger.error(error_message, exc_info=True)
            yield self.create_text_message(text=error_message)
        except Exception as e:
            error_message = f"An unexpected error occurred: {str(e)}"
            logger.error(error_message, exc_info=True)
            yield self.create_text_message(text=error_message)
