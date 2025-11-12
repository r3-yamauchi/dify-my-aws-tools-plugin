"""
場所: tools/s3_operator.py
内容: S3 オブジェクトに対する読み書きおよびプリサイン URL 生成を行うユーティリティツール。
目的: Dify Workflow から S3 上のテキストファイルを簡単に操作できるようにする。
"""

from typing import Any, Union
from urllib.parse import urlparse

import boto3

from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from provider.utils import (
    resolve_aws_credentials,
    build_boto3_client_kwargs,
    reset_clients_on_credential_change,
)

class S3Operator(Tool):
    s3_client: Any = None

    def _invoke(
        self,
        tool_parameters: dict[str, Any],
    ) -> Generator[ToolInvokeMessage]:
        """S3 の read/write 操作を実行し、必要に応じてプリサイン URL を返す."""
        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            if tool_parameters.get("aws_region"):
                credentials["aws_region"] = tool_parameters.get("aws_region")

            reset_clients_on_credential_change(self, credentials, ["s3_client"])

            # S3 クライアントを lazy に初期化
            if not self.s3_client:
                client_kwargs = build_boto3_client_kwargs(credentials)
                self.s3_client = boto3.client("s3", **client_kwargs)

            # S3 URI を解析
            s3_uri = tool_parameters.get("s3_uri")
            if not s3_uri:
                yield self.create_text_message("s3_uri parameter is required")

            parsed_uri = urlparse(s3_uri)
            if parsed_uri.scheme != "s3":
                yield self.create_text_message("Invalid S3 URI format. Must start with 's3://'")

            bucket = parsed_uri.netloc
            key = parsed_uri.path.lstrip("/")  # 先頭のスラッシュを除去

            operation_type = tool_parameters.get("operation_type", "read")
            generate_presign_url = tool_parameters.get("generate_presign_url", False)
            presign_expiry = int(tool_parameters.get("presign_expiry", 3600))  # default 1 hour

            if operation_type == "write":
                text_content = tool_parameters.get("text_content")
                if not text_content:
                    yield self.create_text_message("text_content parameter is required for write operation")

                # テキストを S3 に書き込む
                self.s3_client.put_object(Bucket=bucket, Key=key, Body=text_content.encode("utf-8"))
                result = f"s3://{bucket}/{key}"

                # 必要なら書き込んだオブジェクトのプリサイン URL を返す
                if generate_presign_url:
                    result = self.s3_client.generate_presigned_url(
                        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=presign_expiry
                    )

            else:  # read operation
                # S3 から読み取る
                if generate_presign_url:
                    # 読み込み用のプリサイン URL を返す
                    result = self.s3_client.generate_presigned_url(
                        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=presign_expiry
                    )
                else: 
                    # テキストとして直接取得
                    response = self.s3_client.get_object(Bucket=bucket, Key=key)
                    result = response["Body"].read().decode("utf-8")

                # Generate presigned URL if requested
            yield self.create_text_message(text=result)

        except self.s3_client.exceptions.NoSuchBucket:
            yield self.create_text_message(f"Bucket '{bucket}' does not exist")
        except self.s3_client.exceptions.NoSuchKey:
            yield self.create_text_message(f"Object '{key}' does not exist in bucket '{bucket}'")
        except Exception as e:
            yield self.create_text_message(f"Exception: {str(e)}")
