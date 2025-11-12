"""
場所: tools/sagemaker_text_rerank.py
内容: 既存の検索候補を SageMaker エンドポイントで再スコアリングし、関連度順に並び替えるツール。
目的: Dify の RAG パイプラインで取得した文書候補を高品質の ReRank モデルで絞り込みたいときに利用する。
"""

import json
import operator
from typing import Any, Union
from collections.abc import Generator

import boto3

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from provider.utils import (
    resolve_aws_credentials,
    build_boto3_client_kwargs,
    reset_clients_on_credential_change,
)

class SageMakerReRankTool(Tool):
    sagemaker_client: Any = None
    sagemaker_endpoint: str = None

    def _sagemaker_rerank(self, query_input: str, docs: list[str], rerank_endpoint: str):
        inputs = [query_input] * len(docs)
        response_model = self.sagemaker_client.invoke_endpoint(
            EndpointName=rerank_endpoint,
            Body=json.dumps({"inputs": inputs, "docs": docs}),
            ContentType="application/json",
        )
        json_str = response_model["Body"].read().decode("utf8")
        json_obj = json.loads(json_str)
        scores = json_obj["scores"]
        return scores if isinstance(scores, list) else [scores]

    def _invoke(
        self,
        tool_parameters: dict[str, Any],
    ) -> Generator[ToolInvokeMessage]:
        """入力チェック後に SageMaker リランク API を呼び、結果を JSON で返す."""
        line = 0
        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            if tool_parameters.get("aws_region"):
                credentials["aws_region"] = tool_parameters.get("aws_region")

            reset_clients_on_credential_change(self, credentials, ["sagemaker_client"])

            if not self.sagemaker_client:
                client_kwargs = build_boto3_client_kwargs(credentials)
                self.sagemaker_client = boto3.client("sagemaker-runtime", **client_kwargs)

            line = 1
            if not self.sagemaker_endpoint:
                self.sagemaker_endpoint = tool_parameters.get("sagemaker_endpoint")

            line = 2
            topk = tool_parameters.get("topk", 5)

            line = 3
            query = tool_parameters.get("query", "")
            if not query:
                yield self.create_text_message("Please input query")

            line = 4
            candidate_texts = tool_parameters.get("candidate_texts")
            if not candidate_texts:
                yield self.create_text_message("Please input candidate_texts")

            line = 5
            candidate_docs = json.loads(candidate_texts)
            docs = [item.get("content") for item in candidate_docs]  # モデル入力用に本文のみ抽出

            line = 6
            scores = self._sagemaker_rerank(query_input=query, docs=docs, rerank_endpoint=self.sagemaker_endpoint)

            line = 7
            for idx in range(len(candidate_docs)):
                candidate_docs[idx]["score"] = scores[idx]  # 元の構造にスコアを付与

            line = 8
            sorted_candidate_docs = sorted(candidate_docs, key=operator.itemgetter("score"), reverse=True)

            line = 9
            json_result = {
                "results" : sorted_candidate_docs[:topk]
            }
            yield self.create_json_message(json_result)

        except Exception as e:
            yield self.create_text_message(f"Exception {str(e)}, line : {line}")
