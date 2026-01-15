"""
場所: tools/apply_guardrail.py
内容: Amazon Bedrock Guardrails の ApplyGuardrail API を呼び出してコンテンツ安全性をチェックするツール。
目的: Workflow から追加コードを書かずに Bedrock Guardrail を適用し、違反ポリシーやアクション内容を取得できるようにする。
"""

import json
import logging
from typing import Any, Union, Optional, Literal
from collections.abc import Generator
from pydantic import BaseModel, Field, validator, root_validator

from botocore.exceptions import BotoCoreError  # type: ignore
import boto3  # type: ignore

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from utils.utils import resolve_aws_credentials, build_boto3_client_kwargs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TEXT_CHUNK_SIZE = 1000  # Guardrails 1,000文字単位の課金/評価に合わせて分割

class TextContent(BaseModel):
    text: str = Field(..., description="Text to evaluate")


class ImageContent(BaseModel):
    format: Literal["png", "jpeg"] = Field("png", description="Image format")
    # base64 エンコード済みの画像バイト列
    image_base64: Optional[str] = Field(None, description="Base64 encoded image bytes")
    # S3 URI を直接指定する場合
    image_s3_uri: Optional[str] = Field(None, description="S3 URI for the image")

    @root_validator(pre=True)
    def _at_least_one(cls, values):
        if not values.get("image_base64") and not values.get("image_s3_uri"):
            raise ValueError("Either image_base64 or image_s3_uri is required")
        return values


class ContentItem(BaseModel):
    text: Optional[TextContent] = None
    image: Optional[ImageContent] = None

    @validator("image", always=True)
    def _one_of_text_or_image(cls, v, values):
        if (values.get("text") is None) and v is None:
            raise ValueError("Either text or image must be provided")
        if (values.get("text") is not None) and v is not None:
            raise ValueError("Only one of text or image is allowed per content item")
        return v


class GuardrailParameters(BaseModel):
    guardrail_id: str = Field(..., description="The identifier of the guardrail")
    guardrail_version: str = Field(
        "DRAFT",
        description="The version of the guardrail. Defaults to DRAFT when omitted.",
    )
    source: str = Field("INPUT", description="INPUT or OUTPUT")
    text: Optional[str] = Field(None, description="Text to apply the guardrail to (legacy single input)")
    content: Optional[list[ContentItem]] = Field(None, description="List of content items (text/image)")
    aws_region: Optional[str] = Field(None, description="AWS region for the Bedrock client")

    @validator("guardrail_version", pre=True, always=True)
    def _default_guardrail_version(cls, value: Optional[str]) -> str:
        """guardrail_version が空や未指定の場合は DRAFT を適用する。"""
        if value is None:
            return "DRAFT"
        if isinstance(value, str) and value.strip() == "":
            return "DRAFT"
        return value


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

            request_content = self._build_content(params)

            # 👉 Guardrail API を実行
            response = bedrock_client.apply_guardrail(
                guardrailIdentifier=params.guardrail_id,
                guardrailVersion=params.guardrail_version,
                source=params.source,
                content=request_content,
            )

            logger.info(f"Raw response from AWS: {json.dumps(response, indent=2)}")

            # 👉 応答が空ならユーザーに知らせる
            if not response:
                yield self.create_text_message(text="Received empty response from AWS Bedrock.")

            # 👉 代表的なフィールドを取り出して人が読めるテキストに整形
            action = response.get("action", "No action specified")
            outputs = response.get("outputs", [])
            processed_outputs = response.get("processedOutputs", [])
            output = outputs[0].get("text", "No output received") if outputs else "No output received"
            processed_output = (
                processed_outputs[0].get("text", "No processed output") if processed_outputs else "No processed output"
            )
            assessments = response.get("assessments", [])
            warnings = response.get("warnings")
            action_reasons = response.get("actionReasons")

            # 👉 ポリシー別の評価内容を単純な文字列へ展開
            formatted_assessments = self._format_assessments(assessments)

            result_lines = [
                f"Action: {action}",
                f"Processed Output: {processed_output}",
                f"Raw Output: {output}",
            ]
            if warnings:
                result_lines.append(f"Warnings: {warnings}")
            if action_reasons:
                result_lines.append(f"Action Reasons: {action_reasons}")
            if formatted_assessments:
                result_lines.append("Assessments:")
                result_lines.extend([f"- {item}" for item in formatted_assessments])

            yield self.create_text_message(text="\n".join(result_lines))

            # 構造化データを blob でも返す（LLM 二次利用向け）
            structured_payload = {
                "action": action,
                "processedOutputs": processed_outputs,
                "outputs": outputs,
                "assessments": assessments,
                "warnings": warnings,
                "actionReasons": action_reasons,
            }

            # JSON 形式でも返却し、ワークフロー内で扱いやすくする
            yield self.create_json_message(structured_payload)

            yield self.create_blob_message(
                blob=json.dumps(structured_payload, ensure_ascii=False).encode("utf-8"),
                meta={"mime_type": "application/json"},
            )

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

    def _build_content(self, params: GuardrailParameters) -> list[dict[str, Any]]:
        """content 配列を構築。単一 text 指定は後方互換で分割しながら content に変換。"""

        content_items: list[ContentItem] = []

        if params.content:
            content_items.extend(params.content)
        elif params.text:
            # 長文は 1000 文字チャンクに分割して複数 content とする
            for chunk in self._chunk_text(params.text):
                content_items.append(ContentItem(text=TextContent(text=chunk)))
        else:
            raise ValueError("Either 'content' or 'text' must be provided")

        built: list[dict[str, Any]] = []
        for item in content_items:
            if item.text:
                built.append({"text": {"text": item.text.text}})
            elif item.image:
                image_source: dict[str, Any] = {}
                if item.image.image_base64:
                    image_source["bytes"] = item.image.image_base64
                if item.image.image_s3_uri:
                    image_source["s3Uri"] = item.image.image_s3_uri
                built.append({"image": {"format": item.image.format, "source": image_source}})
        return built

    def _chunk_text(self, text: str) -> list[str]:
        """Guardrails の 1000 文字単位課金/制限を考慮し、必要に応じて分割。"""
        if len(text) <= TEXT_CHUNK_SIZE:
            return [text]
        return [text[i : i + TEXT_CHUNK_SIZE] for i in range(0, len(text), TEXT_CHUNK_SIZE)]

    def _format_assessments(self, assessments: list[dict[str, Any]]) -> list[str]:
        formatted: list[str] = []
        for assessment in assessments:
            for policy_type, policy_data in assessment.items():
                if isinstance(policy_data, dict) and "topics" in policy_data:
                    for topic in policy_data["topics"]:
                        formatted.append(
                            f"Policy={policy_type}, Topic={topic.get('name')}, Type={topic.get('type')}, Action={topic.get('action')}"
                        )
                else:
                    formatted.append(f"Policy={policy_type}, Data={policy_data}")
        return formatted
