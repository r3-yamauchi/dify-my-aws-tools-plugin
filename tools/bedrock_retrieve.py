"""
場所: tools/bedrock_retrieve.py
内容: AWS Bedrock Knowledge Base を検索し、Dify の知識検索フォーマットへ整形するツールの実装。
目的: Workflow / Agent から追加サーバー不要で Bedrock KB を直接参照できるようにする。
"""

import json
import operator
from typing import Any, Optional, Union
from collections.abc import Generator

import boto3

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from provider.utils import (
    resolve_aws_credentials,
    build_boto3_client_kwargs,
    reset_clients_on_credential_change,
)

class BedrockRetrieveTool(Tool):
    bedrock_client: Any = None
    knowledge_base_id: str = None
    topk: int = None

    def convert_to_dify_kb_format(self, kb_repsonse):
        """Bedrock 検索結果を Dify Knowledge 互換の配列に再構築する補助メソッド."""
        result_array = []
        for idx, item in enumerate(kb_repsonse['retrievalResults']):
            # 👉 Bedrock が付与したメタデータをそのまま移し替える
            source_uri = item['metadata']['x-amz-bedrock-kb-source-uri']
            page_number = item['metadata'].get('x-amz-bedrock-kb-document-page-number', 0)
            data_source_id = item['metadata'].get('x-amz-bedrock-kb-data-source-id', '')
            chunk_id = item['metadata'].get('x-amz-bedrock-kb-chunk-id','')
            score = item.get('score', 0.0)

            # 👉 URI 末尾から簡易的にファイル名を作る
            document_name = source_uri.split('/')[-1]

            # 👉 Dify 側の検索結果カードに合わせたキー構成を作る
            metadata = {
                "_source": "knowledge",
                "dataset_id": data_source_id,
                "dataset_name": "BedRock knowledge base",
                "document_id": document_name,
                "document_name": document_name,
                "document_data_source_type": item['content']['type'],
                "segment_id": chunk_id,
                "retriever_from": "workflow",
                "score": round(score, 6),
                "segment_hit_count": 1,  # サンプルでは常に 1 件ヒットとして扱う
                "segment_word_count": len(item['content']['text']),  # 文字数をそのまま語数の近似値として利用
                "segment_position": page_number,
                "doc_metadata": {
                    "tag": "bedrock knowledge base",
                    "source": item["location"]["type"],
                    "uploader": "advantage",
                    "upload_date": int(1715299200),  # デモ用の固定タイムスタンプ
                    "document_name": document_name,
                    "last_update_date": int(1715299200)
                },
                "position": idx + 1
            }

            if item['content']['text'].strip() != "" :
                result_array.append({
                    "content": item['content']['text'],
                    "title": f"{document_name}",  # ここでタイトルを確定
                    "metadata": metadata
                })

        return result_array

    def _bedrock_retrieve(
        self,
        query_input: str,
        knowledge_base_id: str,
        num_results: int,
        search_type: str,
        rerank_model_id: str,
        metadata_filter: Optional[dict] = None,
    ):
        """Bedrock Retrieve API を実行し、必要に応じてリランキングやメタデータフィルターを適用する."""
        try:
            retrieval_query = {"text": query_input}

            if search_type not in ["HYBRID", "SEMANTIC"]:
                raise RuntimeException("search_type should be HYBRID or SEMANTIC")

            # 👉 ベースとなる検索条件（検索タイプ・件数）
            retrieval_configuration = {
                "vectorSearchConfiguration": {"numberOfResults": num_results, "overrideSearchType": search_type}
            }

            if rerank_model_id != "default":
                model_for_rerank_arn = f"arn:aws:bedrock:us-west-2::foundation-model/{rerank_model_id}"
                rerankingConfiguration = {
                    "bedrockRerankingConfiguration": {
                        "numberOfRerankedResults": num_results,
                        "modelConfiguration": {"modelArn": model_for_rerank_arn},
                    },
                    "type": "BEDROCK_RERANKING_MODEL",
                }

                retrieval_configuration["vectorSearchConfiguration"]["rerankingConfiguration"] = rerankingConfiguration
                retrieval_configuration["vectorSearchConfiguration"]["numberOfResults"] = num_results * 5

            # 👉 メタデータフィルタが指定されていればベクター検索条件に混ぜる
            if metadata_filter:
                retrieval_configuration["vectorSearchConfiguration"]["filter"] = metadata_filter

            response = self.bedrock_client.retrieve(
                knowledgeBaseId=knowledge_base_id,
                retrievalQuery=retrieval_query,
                retrievalConfiguration=retrieval_configuration,
            )

            results = self.convert_to_dify_kb_format(response)

            return results
        except Exception as e:
            raise Exception(f"Error retrieving from knowledge base: {str(e)}")

    def _invoke(
        self,
        tool_parameters: dict[str, Any],
    ) -> Generator[ToolInvokeMessage]:
        """Dify から渡されたパラメータを検証し、検索結果を JSON もしくはテキストで返すメインエントリ."""
        try:
            line = 0  # 例外発生時にどの段階か把握するためのステップ番号
            credentials = resolve_aws_credentials(self, tool_parameters)
            reset_clients_on_credential_change(self, credentials, ["bedrock_client"])

            if not self.bedrock_client:
                client_kwargs = build_boto3_client_kwargs(credentials)
                client_kwargs["service_name"] = "bedrock-agent-runtime"
                self.bedrock_client = boto3.client(**client_kwargs)
        except Exception as e:
            yield self.create_text_message(f"Failed to initialize Bedrock client: {str(e)}")

        try:
            line = 1  # Knowledge Base ID のキャッシュが無ければ読み出す
            if not self.knowledge_base_id:
                self.knowledge_base_id = tool_parameters.get("knowledge_base_id")
                if not self.knowledge_base_id:
                    yield self.create_text_message("Please provide knowledge_base_id")

            line = 2  # topk は順次リクエストで変えられるようキャッシュに初期値を保存
            if not self.topk:
                self.topk = tool_parameters.get("topk", 5)

            line = 3  # クエリ未指定の場合は早期リターン
            query = tool_parameters.get("query", "")
            if not query:
                yield self.create_text_message("Please input query")

            # 👉 metadata_filter は JSON 文字列で渡されるためここで dict へ展開
            metadata_filter_str = tool_parameters.get("metadata_filter")
            metadata_filter = json.loads(metadata_filter_str) if metadata_filter_str else None

            search_type = tool_parameters.get("search_type")
            rerank_model_id = tool_parameters.get("rerank_model_id")

            line = 4  # 検索本体の実行
            retrieved_docs = self._bedrock_retrieve(
                query_input=query,
                knowledge_base_id=self.knowledge_base_id,
                num_results=self.topk,
                search_type=search_type,
                rerank_model_id=rerank_model_id,
                metadata_filter=metadata_filter,
            )

            line = 5  # 応答形式に応じた整形
            result_type = tool_parameters.get("result_type")
            if result_type == "json":
                json_result = { "results" : retrieved_docs }
                yield self.create_json_message(json_result)
            else:
                text = ""  # 👉 UI で扱いやすいよう順位 / 本文のみをシリアライズ
                sorted_docs = sorted(
                    retrieved_docs,
                    key=lambda res: res.get("metadata", {}).get("position", 0),
                )
                for i, res in enumerate(sorted_docs):
                    text += f"{i + 1}: {res['content']}\n"
                yield self.create_text_message(text)

        except Exception as e:
            yield self.create_text_message(f"Exception {str(e)}, line : {line}")

    def validate_parameters(self, parameters: dict[str, Any]) -> None:
        """入力必須項目と JSON 文字列を検証し、ワークフロー全体のエラーを減らす."""
        if not parameters.get("knowledge_base_id"):
            raise ValueError("knowledge_base_id is required")

        if not parameters.get("query"):
            raise ValueError("query is required")

        metadata_filter_str = parameters.get("metadata_filter")
        if metadata_filter_str and not isinstance(json.loads(metadata_filter_str), dict):
            raise ValueError("metadata_filter must be a valid JSON object")
