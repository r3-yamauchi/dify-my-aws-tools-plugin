"""
場所: tools/bedrock_retrieve_and_generate.py
内容: Bedrock の Retrieve & Generate API を呼び出して RAG（検索→生成）を一括で実行する Dify 用ツール。
目的: Workflow から単一呼び出しで検索結果と引用付きの回答を得られるようにする。
"""

import json
from typing import Any
from collections.abc import Generator

import boto3

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage
from provider.utils import (
    resolve_aws_credentials,
    build_boto3_client_kwargs,
    reset_clients_on_credential_change,
)

class BedrockRetrieveAndGenerateTool(Tool):
    bedrock_client: Any = None

    def _format_text_with_citations(self, result: dict[str, Any]) -> str:
        """生成結果と引用情報を行単位で整形し、人が読みやすい書式にまとめる."""
        lines = []
        if output := result.get("output"):
            lines.append(output)

        citations = result.get("citations", [])
        if citations:
            lines.append("\n[References]")
            for idx, citation in enumerate(citations, start=1):
                ref_lines = []
                for ref in citation.get("references", []):
                    location = ref.get("location") or ""
                    ref_lines.append(f"- {ref.get('content', '').strip()} {location}".rstrip())
                text = citation.get("text", "").strip()
                joined_refs = "\n".join(ref_lines) if ref_lines else "- (metadata only)"
                lines.append(f"[{idx}] {text}\n{joined_refs}")

        return "\n".join(lines) if lines else ""

    def _invoke(
        self,
        tool_parameters: dict[str, Any],
    ) -> Generator[ToolInvokeMessage]:
        """Bedrock の retrieve_and_generate API を呼び出し、指定フォーマットでレスポンスを返すメイン処理."""
        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            reset_clients_on_credential_change(self, credentials, ["bedrock_client"])

            # 👉 boto3 クライアントをシングルトンにして呼び出しごとのオーバーヘッドを抑える
            if not self.bedrock_client:
                client_kwargs = build_boto3_client_kwargs(credentials)
                client_kwargs["service_name"] = "bedrock-agent-runtime"
                self.bedrock_client = boto3.client(**client_kwargs)
        except Exception as e:
            yield self.create_text_message(f"Failed to initialize Bedrock client: {str(e)}")

        try:
            request_config = {}  # Bedrock API へ送信する設定本体を段階的に組み立てる

            # 👉 LLM 側へ渡すプロンプト（必須）
            input_text = tool_parameters.get("input")
            if input_text:
                request_config["input"] = {"text": input_text}

            # 👉 Bedrock の RAG API は Knowledge Base / External Sources を明示的に選ぶ必要がある
            config_type = tool_parameters.get("type")
            retrieve_generate_config = {"type": config_type}

            # 👉 選択されたモードごとに期待される JSON を埋め込む
            if config_type == "KNOWLEDGE_BASE":
                kb_config_str = tool_parameters.get("knowledge_base_configuration")
                kb_config = json.loads(kb_config_str) if kb_config_str else None
                retrieve_generate_config["knowledgeBaseConfiguration"] = kb_config
            else:  # EXTERNAL_SOURCES
                es_config_str = tool_parameters.get("external_sources_configuration")
                es_config = json.loads(es_config_str) if es_config_str else None
                retrieve_generate_config["externalSourcesConfiguration"] = es_config

            request_config["retrieveAndGenerateConfiguration"] = retrieve_generate_config

            # 👉 セッション設定／セッションID を渡すと Bedrock 側で会話状態を保持できる
            session_config_str = tool_parameters.get("session_configuration")
            session_config = json.loads(session_config_str) if session_config_str else None
            if session_config:
                request_config["sessionConfiguration"] = session_config

            # 👉 セッション ID が明示されていればステートフルに継続実行させる
            session_id = tool_parameters.get("session_id")
            if session_id:
                request_config["sessionId"] = session_id

            # 👉 ここまでで構築した設定を Bedrock へ送信
            response = self.bedrock_client.retrieve_and_generate(**request_config)

            # 👉 Bedrock から返る本文と引用情報を Dify 側で使いやすい dict に変換
            result = {"output": response.get("output", {}).get("text", ""), "citations": []}

            # 👉 引用リストは UI 表示 / 後段プロンプト双方で扱える構造に揃える
            for citation in response.get("citations", []):
                citation_info = {
                    "text": citation.get("generatedResponsePart", {}).get("textResponsePart", {}).get("text", ""),
                    "references": [],
                }

                for ref in citation.get("retrievedReferences", []):
                    reference = {
                        "content": ref.get("content", {}).get("text", ""),
                        "metadata": ref.get("metadata", {}),
                        "location": None,
                    }

                    location = ref.get("location", {})
                    if location.get("type") == "S3":
                        reference["location"] = location.get("s3Location", {}).get("uri")

                    citation_info["references"].append(reference)

                result["citations"].append(citation_info)
            result_type = tool_parameters.get("result_type")
            if result_type == "json":
                yield self.create_json_message(result)
            elif result_type == "text-with-citations":
                text_with_refs = self._format_text_with_citations(result)
                yield self.create_text_message(text_with_refs)
            else:
                yield self.create_text_message(result.get("output"))
        except json.JSONDecodeError as e:
            yield self.create_text_message(f"Invalid JSON format: {str(e)}")
        except Exception as e:
            yield self.create_text_message(f"Tool invocation error: {str(e)}")

    def validate_parameters(self, parameters: dict[str, Any]) -> None:
        """必須パラメータや JSON 文字列の整合性を検証して実行前に弾く."""
        # 👉 入力必須の基本パラメータをチェック
        if not parameters.get("input"):
            raise ValueError("input is required")
        if not parameters.get("type"):
            raise ValueError("type is required")

        # 👉 JSON 文字列で渡される構成情報を事前に validate
        json_configs = ["knowledge_base_configuration", "external_sources_configuration", "session_configuration"]
        for config in json_configs:
            if config_value := parameters.get(config):
                try:
                    json.loads(config_value)
                except json.JSONDecodeError:
                    raise ValueError(f"{config} must be a valid JSON string")

        # 👉 type が想定値かどうか確認
        config_type = parameters.get("type")
        if config_type not in ["KNOWLEDGE_BASE", "EXTERNAL_SOURCES"]:
            raise ValueError("type must be either KNOWLEDGE_BASE or EXTERNAL_SOURCES")

        # 👉 type ごとの必須設定が欠けていないかチェック
        if config_type == "KNOWLEDGE_BASE" and not parameters.get("knowledge_base_configuration"):
            raise ValueError("knowledge_base_configuration is required when type is KNOWLEDGE_BASE")
        elif config_type == "EXTERNAL_SOURCES" and not parameters.get("external_sources_configuration"):
            raise ValueError("external_sources_configuration is required when type is EXTERNAL_SOURCES")
