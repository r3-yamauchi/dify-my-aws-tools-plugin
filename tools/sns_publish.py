"""
場所: tools/sns_publish.py
内容: Amazon SNS トピックへメッセージを公開する Dify ツール。
目的: Dify フローからコードを書かずに SNS へ通知を送る。
"""

import json
import logging
from typing import Any
from collections.abc import Generator

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from dify_plugin import Tool
from dify_plugin.entities.tool import (
    ToolInvokeMessage,
    ToolParameter,
    ToolParameterOption,
    I18nObject,
)
from utils.utils import resolve_aws_credentials, build_boto3_client_kwargs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SnsPublishTool(Tool):
    """Publish a message to an SNS topic."""

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        topic_arn = tool_parameters.get("topic_arn", "").strip()
        topic_name = tool_parameters.get("topic_name", "").strip()
        message = tool_parameters.get("message", "")
        subject = tool_parameters.get("subject")
        message_attributes_raw = tool_parameters.get("message_attributes", "")

        if not topic_arn and not topic_name:
            yield self.create_text_message("Please provide topic_arn or topic_name")
            return
        if not message:
            yield self.create_text_message("Please provide message")
            return

        message_attributes = None
        if message_attributes_raw:
            try:
                parsed = json.loads(message_attributes_raw)
                if not isinstance(parsed, dict):
                    raise ValueError("message_attributes must be a JSON object")
                # SNS は {"Key": {"DataType": "String", "StringValue": "..."}} 形式を期待する
                message_attributes = parsed
            except Exception as exc:  # noqa: BLE001
                yield self.create_text_message(f"Invalid message_attributes: {exc}")
                return

        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            client_kwargs = build_boto3_client_kwargs(credentials)
            sns = boto3.client("sns", **client_kwargs)

            resolved_topic_arn = topic_arn
            if not resolved_topic_arn:
                region = client_kwargs.get("region_name") or tool_parameters.get("aws_region") or "us-east-1"
                # STS でアカウント ID を取得
                sts = boto3.client("sts", **client_kwargs)
                account_id = sts.get_caller_identity()["Account"]
                resolved_topic_arn = f"arn:aws:sns:{region}:{account_id}:{topic_name}"

            publish_kwargs: dict[str, Any] = {"TopicArn": resolved_topic_arn, "Message": message}
            if subject:
                publish_kwargs["Subject"] = subject[:100]  # SNS の件名は最大100文字
            if message_attributes:
                publish_kwargs["MessageAttributes"] = message_attributes

            response = sns.publish(**publish_kwargs)
            message_id = response.get("MessageId")
            yield self.create_text_message(f"Message published to {topic_arn} (MessageId={message_id})")

        except (BotoCoreError, ClientError) as exc:
            logger.error("SNS publish failed", exc_info=True)
            yield self.create_text_message(f"AWS error: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.error("Unexpected error", exc_info=True)
            yield self.create_text_message(f"Failed to publish message: {exc}")

    def get_runtime_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="topic_arn",
                label=I18nObject(en_US="SNS Topic ARN", ja_JP="SNS トピック ARN"),
                type=ToolParameter.ToolParameterType.STRING,
                required=True,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US="The SNS Topic ARN to publish to", ja_JP="メッセージを送信する SNS Topic ARN"
                ),
            ),
            ToolParameter(
                name="message",
                label=I18nObject(en_US="Message", ja_JP="メッセージ本文"),
                type=ToolParameter.ToolParameterType.STRING,
                required=True,
                form=ToolParameter.ToolParameterForm.LLM,
                human_description=I18nObject(
                    en_US="Message body to send", ja_JP="送信するメッセージ本文"
                ),
            ),
            ToolParameter(
                name="subject",
                label=I18nObject(en_US="Subject", ja_JP="件名 (任意)"),
                type=ToolParameter.ToolParameterType.STRING,
                required=False,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US="Optional subject (max 100 characters)", ja_JP="任意の件名（最大100文字）"
                ),
            ),
            ToolParameter(
                name="message_attributes",
                label=I18nObject(en_US="Message Attributes JSON", ja_JP="メッセージ属性 JSON"),
                type=ToolParameter.ToolParameterType.STRING,
                required=False,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US=(
                        "SNS MessageAttributes as JSON object, e.g. "
                        "{\"key\": {\"DataType\": \"String\", \"StringValue\": \"value\"}}"
                    ),
                    ja_JP=(
                        "SNS MessageAttributes を JSON で指定。例: "
                        "{\"key\": {\"DataType\": \"String\", \"StringValue\": \"value\"}}"
                    ),
                ),
            ),
        ]
