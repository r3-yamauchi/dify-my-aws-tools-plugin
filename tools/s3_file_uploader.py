"""
場所: tools/s3_file_uploader.py
内容: Dify ワークフローから受け取ったファイルを指定の S3 バケットへアップロードするツール。
目的: 先行ノードで生成したバイナリ資産を S3 へ安全かつ簡潔に保存し、後続ノードで再利用できるようにする。
"""

from __future__ import annotations

import uuid
from collections.abc import Generator
from typing import Any

import boto3
from botocore.exceptions import ClientError

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


def _sanitize_prefix(prefix: str | None) -> str:
    """キーの先頭や末尾のスラッシュを整理し、空文字でも文字列を返す補助関数。"""
    if not prefix:
        return ""
    return prefix.strip("/ ")


class S3FileUploader(Tool):
    s3_client: Any = None

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """ファイルを取得し、S3 へアップロードした結果を JSON で返す。"""
        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            if tool_parameters.get("aws_region"):
                credentials["aws_region"] = tool_parameters["aws_region"]

            reset_clients_on_credential_change(self, credentials, ["s3_client"])
            if not self.s3_client:
                client_kwargs = build_boto3_client_kwargs(credentials)
                self.s3_client = boto3.client("s3", **client_kwargs)
        except Exception as exc:
            yield self.create_text_message(f"Failed to initialize AWS client: {exc}")
            return

        input_file = tool_parameters.get("input_file")
        if not input_file:
            yield self.create_text_message("input_file parameter is required")
            return

        try:
            file_bytes: bytes = input_file.blob  # type: ignore[attr-defined]
        except Exception as exc:
            yield self.create_text_message(f"Failed to read input_file: {exc}")
            return

        bucket_name = tool_parameters.get("bucket_name")
        if not bucket_name:
            yield self.create_text_message("bucket_name parameter is required")
            return

        key_prefix = _sanitize_prefix(tool_parameters.get("key_prefix"))
        requested_key = tool_parameters.get("object_key") or getattr(input_file, "filename", None)
        fallback_key = getattr(input_file, "url", "").rstrip("/").split("/")[-1] if getattr(input_file, "url", None) else None
        object_key = requested_key or fallback_key or f"dify-upload-{uuid.uuid4().hex}"
        object_key = object_key.lstrip("/")
        if key_prefix:
            object_key = f"{key_prefix}/{object_key}"

        content_type = getattr(input_file, "mime_type", None) or "application/octet-stream"

        try:
            self.s3_client.put_object(
                Bucket=bucket_name,
                Key=object_key,
                Body=file_bytes,
                ContentType=content_type,
            )
        except ClientError as exc:
            error_message = exc.response.get("Error", {}).get("Message", str(exc))
            yield self.create_text_message(f"Failed to upload file to S3: {error_message}")
            return

        s3_uri = f"s3://{bucket_name}/{object_key}"
        result_payload: dict[str, Any] = {
            "bucket_name": bucket_name,
            "object_key": object_key,
            "s3_uri": s3_uri,
        }

        text_message = None
        if tool_parameters.get("generate_presign_url"):
            expiry_seconds = int(tool_parameters.get("presign_expiry", 3600))
            try:
                presigned_url = self.s3_client.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": bucket_name, "Key": object_key},
                    ExpiresIn=expiry_seconds,
                )
                result_payload["presigned_url"] = presigned_url
                result_payload["presign_expiry"] = expiry_seconds
                text_message = self.create_text_message(presigned_url)
            except ClientError as exc:
                error_message = exc.response.get("Error", {}).get("Message", str(exc))
                yield self.create_text_message(f"Upload succeeded but failed to create presigned URL: {error_message}")
                return
        else:
            text_message = self.create_text_message(s3_uri)

        yield self.create_json_message(result_payload)
        if text_message:
            yield text_message
