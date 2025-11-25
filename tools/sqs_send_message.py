"""
場所: tools/sqs_send_message.py
内容: Amazon SQS キューへメッセージを送信する Dify ツール。
目的: Dify フローからコードレスで SQS にメッセージをエンキューする。
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
from provider.utils import resolve_aws_credentials, build_boto3_client_kwargs

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SqsSendMessageTool(Tool):
    """Send a message to an SQS queue."""

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage]:
        queue_url = tool_parameters.get("queue_url", "").strip()
        queue_name = tool_parameters.get("queue_name", "").strip()
        message_body = tool_parameters.get("message_body", "")
        delay_seconds = tool_parameters.get("delay_seconds")
        message_attributes_raw = tool_parameters.get("message_attributes", "")

        if not queue_url and not queue_name:
            yield self.create_text_message("Please provide either queue_url or queue_name")
            return
        if not message_body:
            yield self.create_text_message("Please provide message_body")
            return

        message_attributes = None
        if message_attributes_raw:
            try:
                parsed = json.loads(message_attributes_raw)
                if not isinstance(parsed, dict):
                    raise ValueError("message_attributes must be a JSON object")
                message_attributes = parsed
            except Exception as exc:  # noqa: BLE001
                yield self.create_text_message(f"Invalid message_attributes: {exc}")
                return

        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            client_kwargs = build_boto3_client_kwargs(credentials)
            sqs = boto3.client("sqs", **client_kwargs)

            resolved_queue_url = queue_url
            if not resolved_queue_url:
                try:
                    resolved_queue_url = sqs.get_queue_url(QueueName=queue_name)["QueueUrl"]
                except Exception as exc:  # noqa: BLE001
                    yield self.create_text_message(f"Failed to resolve queue_name: {exc}")
                    return

            send_kwargs: dict[str, Any] = {"QueueUrl": resolved_queue_url, "MessageBody": message_body}
            if delay_seconds is not None:
                try:
                    send_kwargs["DelaySeconds"] = int(delay_seconds)
                except ValueError:
                    yield self.create_text_message("delay_seconds must be an integer")
                    return
            if message_attributes:
                send_kwargs["MessageAttributes"] = message_attributes

            response = sqs.send_message(**send_kwargs)
            message_id = response.get("MessageId")
            yield self.create_text_message(f"Message enqueued to SQS (MessageId={message_id})")

        except (BotoCoreError, ClientError) as exc:
            logger.error("SQS send_message failed", exc_info=True)
            yield self.create_text_message(f"AWS error: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.error("Unexpected error", exc_info=True)
            yield self.create_text_message(f"Failed to enqueue message: {exc}")

    def get_runtime_parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="queue_url",
                label=I18nObject(en_US="SQS Queue URL", ja_JP="SQS キュー URL"),
                type=ToolParameter.ToolParameterType.STRING,
                required=False,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US="The SQS Queue URL to send the message to",
                    ja_JP="メッセージを送信する SQS キューの URL",
                ),
            ),
            ToolParameter(
                name="queue_name",
                label=I18nObject(en_US="SQS Queue Name", ja_JP="SQS キュー名"),
                type=ToolParameter.ToolParameterType.STRING,
                required=False,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US="Queue name to resolve via GetQueueUrl if queue_url is not provided",
                    ja_JP="queue_url が無い場合に GetQueueUrl で解決するキュー名",
                ),
            ),
            ToolParameter(
                name="message_body",
                label=I18nObject(en_US="Message Body", ja_JP="メッセージ本文"),
                type=ToolParameter.ToolParameterType.STRING,
                required=True,
                form=ToolParameter.ToolParameterForm.LLM,
                human_description=I18nObject(
                    en_US="Message body to enqueue", ja_JP="キューに送るメッセージ本文"
                ),
            ),
            ToolParameter(
                name="delay_seconds",
                label=I18nObject(en_US="Delay Seconds", ja_JP="遅延秒数"),
                type=ToolParameter.ToolParameterType.NUMBER,
                required=False,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US="Optional delay in seconds before the message becomes visible",
                    ja_JP="メッセージが可視化されるまでの遅延秒（任意）",
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
                        "SQS MessageAttributes as JSON object, e.g. "
                        "{\"Key\": {\"DataType\": \"String\", \"StringValue\": \"value\"}}"
                    ),
                    ja_JP=(
                        "SQS MessageAttributes を JSON で指定。例: "
                        "{\"Key\": {\"DataType\": \"String\", \"StringValue\": \"value\"}}"
                    ),
                ),
            ),
        ]
