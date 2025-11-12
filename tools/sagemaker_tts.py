"""
場所: tools/sagemaker_tts.py
内容: SageMaker 上のカスタム TTS エンドポイントを呼び出し、音声合成結果の S3 プリサイン URL を返すツール。
目的: Dify Workflow からプリセット音声／クローン音声／指示付き音声など複数モードで簡易に音声生成できるようにする。
"""

import json
from enum import Enum
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


class TTSModelType(Enum):
    PresetVoice = "PresetVoice"
    CloneVoice = "CloneVoice"
    CloneVoice_CrossLingual = "CloneVoice_CrossLingual"
    InstructVoice = "InstructVoice"


class SageMakerTTSTool(Tool):
    sagemaker_client: Any = None
    sagemaker_endpoint: str | None = None
    s3_client: Any = None
    comprehend_client: Any = None

    def _detect_lang_code(self, content: str, map_dict: Optional[dict] = None):
        """Comprehend で言語を推定し、モデルが期待する言語タグへ変換する."""
        map_dict = {"zh": "<|zh|>", "en": "<|en|>", "ja": "<|jp|>", "zh-TW": "<|yue|>", "ko": "<|ko|>"}

        response = self.comprehend_client.detect_dominant_language(Text=content)
        language_code = response["Languages"][0]["LanguageCode"]
        return map_dict.get(language_code, "<|zh|>")

    def _build_tts_payload(
        self,
        model_type: str,
        content_text: str,
        model_role: str,
        prompt_text: str,
        prompt_audio: str,
        instruct_text: str,
    ):
        # モードに応じたペイロードを生成
        if model_type == TTSModelType.PresetVoice.value and model_role:
            return {"tts_text": content_text, "role": model_role}
        if model_type == TTSModelType.CloneVoice.value and prompt_text and prompt_audio:
            return {"tts_text": content_text, "prompt_text": prompt_text, "prompt_audio": prompt_audio}
        if model_type == TTSModelType.CloneVoice_CrossLingual.value and prompt_audio:
            lang_tag = self._detect_lang_code(content_text)
            return {"tts_text": f"{content_text}", "prompt_audio": prompt_audio, "lang_tag": lang_tag}
        if model_type == TTSModelType.InstructVoice.value and instruct_text and model_role:
            return {"tts_text": content_text, "role": model_role, "instruct_text": instruct_text}

        raise RuntimeError(f"Invalid params for {model_type}")

    def _invoke_sagemaker(self, payload: dict, endpoint: str):
        """SageMaker Runtime へリクエストを送り JSON レスポンスを取得する."""
        response_model = self.sagemaker_client.invoke_endpoint(
            EndpointName=endpoint,
            Body=json.dumps(payload),
            ContentType="application/json",
        )
        json_str = response_model["Body"].read().decode("utf8")
        json_obj = json.loads(json_str)
        return json_obj

    def _invoke(
        self,
        tool_parameters: dict[str, Any],
    ) -> Generator[ToolInvokeMessage]:
        """音声合成パラメータを組み立て、SageMaker 推論を実行する."""
        try:
            credentials = resolve_aws_credentials(self, tool_parameters)
            if tool_parameters.get("aws_region"):
                credentials["aws_region"] = tool_parameters.get("aws_region")

            reset_clients_on_credential_change(
                self,
                credentials,
                ["sagemaker_client", "s3_client", "comprehend_client"],
            )

            if not self.sagemaker_client:
                client_kwargs = build_boto3_client_kwargs(credentials)
                self.sagemaker_client = boto3.client("sagemaker-runtime", **client_kwargs)
                self.s3_client = boto3.client("s3", **client_kwargs)
                self.comprehend_client = boto3.client("comprehend", **client_kwargs)

            if not self.sagemaker_endpoint:
                self.sagemaker_endpoint = tool_parameters.get("sagemaker_endpoint")

            tts_text = tool_parameters.get("tts_text")
            tts_infer_type = tool_parameters.get("tts_infer_type")

            voice = tool_parameters.get("voice")
            mock_voice_audio = tool_parameters.get("mock_voice_audio")
            mock_voice_text = tool_parameters.get("mock_voice_text")
            voice_instruct_prompt = tool_parameters.get("voice_instruct_prompt")
            payload = self._build_tts_payload(
                tts_infer_type, tts_text, voice, mock_voice_text, mock_voice_audio, voice_instruct_prompt
            )

            result = self._invoke_sagemaker(payload, self.sagemaker_endpoint)

            yield self.create_text_message(text=result["s3_presign_url"])

        except Exception as e:
            yield self.create_text_message(f"Exception {str(e)}")
