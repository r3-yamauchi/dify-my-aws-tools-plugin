"""
場所: tools/s3_create_bucket.py
内容: 指定された名前とリージョンで S3 バケットを作成し、結果を返すツール。
目的: ワークフロー内からバケットを安全に新規作成し、後続処理での利用を容易にする。
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import boto3
from botocore.exceptions import ClientError

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

try:  # pragma: no cover - import path differs when packaged
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


class S3CreateBucket(Tool):
    s3_client: Any = None

    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        """S3 バケットを作成し、作成結果のサマリを返す。"""

        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            if tool_parameters.get("aws_region"):
                credentials["aws_region"] = tool_parameters["aws_region"]

            reset_clients_on_credential_change(self, credentials, ["s3_client"])
            if not self.s3_client:
                client_kwargs = build_boto3_client_kwargs(credentials)
                self.s3_client = boto3.client("s3", **client_kwargs)
        except Exception as exc:  # pragma: no cover - boto3 init errors
            yield self.create_text_message(f"Failed to initialize AWS client: {exc}")
            return

        bucket_name = (tool_parameters.get("bucket_name") or "").strip()
        if not bucket_name:
            yield self.create_text_message("bucket_name parameter is required")
            return

        region = credentials.get("aws_region") or "us-east-1"
        acl = (tool_parameters.get("acl") or "").strip()

        create_kwargs: dict[str, Any] = {"Bucket": bucket_name}
        if acl:
            create_kwargs["ACL"] = acl

        if region != "us-east-1":
            create_kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}

        try:
            response = self.s3_client.create_bucket(**create_kwargs)
        except ClientError as exc:
            error_message = exc.response.get("Error", {}).get("Message", str(exc))
            yield self.create_text_message(f"Failed to create bucket: {error_message}")
            return
        except Exception as exc:  # pragma: no cover - unexpected errors
            yield self.create_text_message(f"Failed to create bucket: {exc}")
            return

        location = response.get("Location")
        payload = {
            "bucket_name": bucket_name,
            "region": region,
            "acl": acl or "private",
            "location": location or f"/{bucket_name}",
            "s3_uri": f"s3://{bucket_name}/",
        }

        yield self.create_json_message(payload)
        yield self.create_text_message(
            f"Bucket '{bucket_name}' created in {region}. Location: {payload['location']}"
        )
