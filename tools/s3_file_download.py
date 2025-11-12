"""
場所: tools/s3_file_download.py
内容: S3 からファイルを取得し、Dify ワークフロー内で扱えるバイナリおよびメタデータ変数を返すダウンロード専用ツール。
目的: s3_operator の読み込み機能を独立させ、ワークフロー後続ノードへファイルをそのまま渡せるようにする。
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from urllib.parse import urlparse

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


def _build_metadata_text(metadata: dict[str, Any]) -> str:
    """シンプルなキー=値形式のテキストへ整形する。"""
    lines = []
    for key, value in metadata.items():
        if value is None:
            continue
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


class S3FileDownload(Tool):
    s3_client: Any = None

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """S3 からファイルを取得し、バイナリとメタデータ（JSON/テキスト）を返す。"""
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

        s3_uri = tool_parameters.get("s3_uri")
        if not s3_uri:
            yield self.create_text_message("s3_uri parameter is required")
            return

        parsed_uri = urlparse(s3_uri)
        if parsed_uri.scheme != "s3" or not parsed_uri.netloc or not parsed_uri.path:
            yield self.create_text_message("Invalid S3 URI format. Use s3://bucket/key")
            return

        bucket = parsed_uri.netloc
        key = parsed_uri.path.lstrip("/")

        try:
            response = self.s3_client.get_object(Bucket=bucket, Key=key)
            file_bytes = response["Body"].read()
        except self.s3_client.exceptions.NoSuchBucket:
            yield self.create_text_message(f"Bucket '{bucket}' does not exist")
            return
        except self.s3_client.exceptions.NoSuchKey:
            yield self.create_text_message(f"Object '{key}' does not exist in bucket '{bucket}'")
            return
        except ClientError as exc:
            error_message = exc.response.get("Error", {}).get("Message", str(exc))
            yield self.create_text_message(f"Failed to download S3 object: {error_message}")
            return
        except Exception as exc:
            yield self.create_text_message(f"Failed to download S3 object: {exc}")
            return

        filename = key.split("/")[-1] if key else "downloaded_file"
        content_type = response.get("ContentType") or "application/octet-stream"
        metadata_dict = {
            "bucket": bucket,
            "key": key,
            "content_type": content_type,
            "content_length": response.get("ContentLength"),
            "etag": response.get("ETag"),
            "last_modified": response.get("LastModified").isoformat()
            if response.get("LastModified")
            else None,
            "s3_uri": s3_uri,
        }
        metadata_text = _build_metadata_text(metadata_dict)

        blob_meta = {
            "filename": filename,
            "mime_type": content_type,
            "s3_uri": s3_uri,
        }
        yield self.create_blob_message(file_bytes, meta=blob_meta)

        yield self.create_json_message(metadata_dict)
        yield self.create_text_message(metadata_text or f"bucket: {bucket}\nkey: {key}")
