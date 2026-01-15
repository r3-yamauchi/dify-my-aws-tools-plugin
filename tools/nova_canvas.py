"""
場所: tools/nova_canvas.py
内容: AWS Bedrock Nova Canvas モデルを利用して画像生成・編集を行うツール。
目的: Dify からのテキスト/画像入力を Nova Canvas API に橋渡しし、S3 へ結果を保存する。
"""

import base64
import json
import logging
import re
from datetime import datetime
from typing import Any, Union
from urllib.parse import urlparse
from collections.abc import Generator

import boto3

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


class NovaCanvasTool(Tool):
    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage]:
        """
        Invoke AWS Bedrock Nova Canvas model for image generation
        """
        # 共通パラメータを取得
        prompt = tool_parameters.get("prompt", "")
        image_output_s3uri = tool_parameters.get("image_output_s3uri", "").strip()
        if not prompt:
            yield self.create_text_message("Please provide a text prompt for image generation.")
        if not image_output_s3uri or urlparse(image_output_s3uri).scheme != "s3":
            yield self.create_text_message("Please provide an valid S3 URI for image output.")

        task_type = tool_parameters.get("task_type", "TEXT_IMAGE")
        aws_region = tool_parameters.get("aws_region")

        # 画像生成共通設定
        width = tool_parameters.get("width", 1024)
        height = tool_parameters.get("height", 1024)
        cfg_scale = tool_parameters.get("cfg_scale", 8.0)
        negative_prompt = tool_parameters.get("negative_prompt", "")
        seed = tool_parameters.get("seed", 0)
        quality = tool_parameters.get("quality", "standard")

        credentials = resolve_aws_credentials(self, tool_parameters)
        if aws_region:
            credentials["aws_region"] = aws_region
        client_kwargs = build_boto3_client_kwargs(credentials)

        # 入力画像がある場合は S3 から取得し Base64 へエンコード
        image_input_s3uri = tool_parameters.get("image_input_s3uri", "")
        if task_type != "TEXT_IMAGE":
            if not image_input_s3uri or urlparse(image_input_s3uri).scheme != "s3":
                yield self.create_text_message("Please provide a valid S3 URI for image to image generation.")

            # S3 URI を解析
            parsed_uri = urlparse(image_input_s3uri)
            bucket = parsed_uri.netloc
            key = parsed_uri.path.lstrip("/")

            # S3 クライアントを初期化して画像をダウンロード
            s3_client = boto3.client("s3", **client_kwargs)
            response = s3_client.get_object(Bucket=bucket, Key=key)
            image_data = response["Body"].read()

            # 画像を Base64 エンコード
            input_image = base64.b64encode(image_data).decode("utf-8")

        try:
            # Bedrock クライアントを初期化
            bedrock = boto3.client(service_name="bedrock-runtime", **client_kwargs)

            # Nova Canvas の基本設定
            image_generation_config = {
                "width": width,
                "height": height,
                "cfgScale": cfg_scale,
                "seed": seed,
                "numberOfImages": 1,
                "quality": quality,
            }

            # タスクタイプに応じてリクエストボディを組み立てる
            body = {"imageGenerationConfig": image_generation_config}

            if task_type == "TEXT_IMAGE":
                body["taskType"] = "TEXT_IMAGE"
                body["textToImageParams"] = {"text": prompt}
                if negative_prompt:
                    body["textToImageParams"]["negativeText"] = negative_prompt

            elif task_type == "COLOR_GUIDED_GENERATION":
                colors = tool_parameters.get("colors", "#ff8080-#ffb280-#ffe680-#ffe680")
                if not self._validate_color_string(colors):
                    yield self.create_text_message("Please provide valid colors in hexadecimal format.")

                body["taskType"] = "COLOR_GUIDED_GENERATION"
                body["colorGuidedGenerationParams"] = {
                    "colors": colors.split("-"),
                    "referenceImage": input_image,
                    "text": prompt,
                }
                if negative_prompt:
                    body["colorGuidedGenerationParams"]["negativeText"] = negative_prompt

            elif task_type == "IMAGE_VARIATION":
                similarity_strength = tool_parameters.get("similarity_strength", 0.5)

                body["taskType"] = "IMAGE_VARIATION"
                body["imageVariationParams"] = {
                    "images": [input_image],
                    "similarityStrength": similarity_strength,
                    "text": prompt,
                }
                if negative_prompt:
                    body["imageVariationParams"]["negativeText"] = negative_prompt

            elif task_type == "INPAINTING":
                mask_prompt = tool_parameters.get("mask_prompt")
                if not mask_prompt:
                    yield self.create_text_message("Please provide a mask prompt for image inpainting.")

                body["taskType"] = "INPAINTING"
                body["inPaintingParams"] = {"image": input_image, "maskPrompt": mask_prompt, "text": prompt}
                if negative_prompt:
                    body["inPaintingParams"]["negativeText"] = negative_prompt

            elif task_type == "OUTPAINTING":
                mask_prompt = tool_parameters.get("mask_prompt")
                if not mask_prompt:
                    yield self.create_text_message("Please provide a mask prompt for image outpainting.")
                outpainting_mode = tool_parameters.get("outpainting_mode", "DEFAULT")

                body["taskType"] = "OUTPAINTING"
                body["outPaintingParams"] = {
                    "image": input_image,
                    "maskPrompt": mask_prompt,
                    "outPaintingMode": outpainting_mode,
                    "text": prompt,
                }
                if negative_prompt:
                    body["outPaintingParams"]["negativeText"] = negative_prompt

            elif task_type == "BACKGROUND_REMOVAL":
                body["taskType"] = "BACKGROUND_REMOVAL"
                body["backgroundRemovalParams"] = {"image": input_image}

            else:
                yield self.create_text_message(f"Unsupported task type: {task_type}")

            # Nova Canvas モデルを呼び出し
            response = bedrock.invoke_model(
                body=json.dumps(body),
                modelId="amazon.nova-canvas-v1:0",
                accept="application/json",
                contentType="application/json",
            )

            # レスポンスをパースし、生成された画像を取得
            response_body = json.loads(response.get("body").read())
            if response_body.get("error"):
                raise Exception(f"Error in model response: {response_body.get('error')}")
            base64_image = response_body.get("images")[0]

            # 出力先 S3 URI が指定されていればアップロード
            try:
                # S3 URI 解析とファイル名生成
                parsed_uri = urlparse(image_output_s3uri)
                output_bucket = parsed_uri.netloc
                output_base_path = parsed_uri.path.lstrip("/")
                # タイムスタンプ付きのファイル名を生成
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                output_key = f"{output_base_path}/canvas-output-{timestamp}.png"

                # S3 へ PNG をアップロード
                s3_client = boto3.client("s3", **client_kwargs)

                # Base64 画像をデコードして S3 にアップロード
                image_data = base64.b64decode(base64_image)
                s3_client.put_object(Bucket=output_bucket, Key=output_key, Body=image_data, ContentType="image/png")
                logger.info(f"Image uploaded to s3://{output_bucket}/{output_key}")
            except Exception as e:
                logger.exception("Failed to upload image to S3")
            # 画像を返す
            yield self.create_text_message(f"s3://{output_bucket}/{output_key}")
            yield self.create_blob_message(
                blob=base64.b64decode(base64_image),
                meta={"mime_type": "image/png"},
            )

        except Exception as e:
            yield self.create_text_message(f"Failed to generate image: {str(e)}")

    def _validate_color_string(self, color_string) -> bool:
        color_pattern = r"^#[0-9a-fA-F]{6}(?:-#[0-9a-fA-F]{6})*$"

        if re.match(color_pattern, color_string):
            return True
        return False

    def get_runtime_parameters(self) -> list[ToolParameter]:
        parameters = [
            ToolParameter(
                name="prompt",
                label=I18nObject(en_US="Prompt", ja_JP="プロンプト"),
                type=ToolParameter.ToolParameterType.STRING,
                required=True,
                form=ToolParameter.ToolParameterForm.LLM,
                human_description=I18nObject(
                    en_US="Text description of the image you want to generate or modify",
                    ja_JP="生成または編集したい画像のテキスト説明",
                ),
                llm_description="Describe the image you want to generate or how you want to modify the input image",
            ),
            ToolParameter(
                name="image_input_s3uri",
                label=I18nObject(en_US="Input image s3 uri", ja_JP="入力画像の S3 URI"),
                type=ToolParameter.ToolParameterType.STRING,
                required=False,
                form=ToolParameter.ToolParameterForm.LLM,
                human_description=I18nObject(en_US="Image to be modified", ja_JP="編集対象の画像"),
            ),
            ToolParameter(
                name="image_output_s3uri",
                label=I18nObject(en_US="Output Image S3 URI", ja_JP="出力画像の S3 URI ディレクトリ"),
                type=ToolParameter.ToolParameterType.STRING,
                required=True,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US="S3 URI where the generated image should be uploaded",
                    ja_JP="生成画像をアップロードする S3 URI",
                ),
            ),
            ToolParameter(
                name="width",
                label=I18nObject(en_US="Width", ja_JP="幅"),
                type=ToolParameter.ToolParameterType.NUMBER,
                required=False,
                default=1024,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(en_US="Width of the generated image", ja_JP="生成画像の幅"),
            ),
            ToolParameter(
                name="height",
                label=I18nObject(en_US="Height", ja_JP="高さ"),
                type=ToolParameter.ToolParameterType.NUMBER,
                required=False,
                default=1024,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(en_US="Height of the generated image", ja_JP="生成画像の高さ"),
            ),
            ToolParameter(
                name="cfg_scale",
                label=I18nObject(en_US="CFG Scale", ja_JP="CFG スケール"),
                type=ToolParameter.ToolParameterType.NUMBER,
                required=False,
                default=8.0,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US="How strongly the image should conform to the prompt",
                    ja_JP="画像をプロンプトにどれだけ従わせるか",
                ),
            ),
            ToolParameter(
                name="negative_prompt",
                label=I18nObject(en_US="Negative Prompt", ja_JP="ネガティブプロンプト"),
                type=ToolParameter.ToolParameterType.STRING,
                required=False,
                default="",
                form=ToolParameter.ToolParameterForm.LLM,
                human_description=I18nObject(
                    en_US="Things you don't want in the generated image",
                    ja_JP="生成画像に含めたくない要素",
                ),
            ),
            ToolParameter(
                name="seed",
                label=I18nObject(en_US="Seed", ja_JP="シード値"),
                type=ToolParameter.ToolParameterType.NUMBER,
                required=False,
                default=0,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(en_US="Random seed for image generation", ja_JP="画像生成の乱数シード"),
            ),
            ToolParameter(
                name="aws_region",
                label=I18nObject(en_US="AWS Region", ja_JP="AWS リージョン"),
                type=ToolParameter.ToolParameterType.STRING,
                required=False,
                default="us-east-1",
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(en_US="AWS region for Bedrock service", ja_JP="Bedrock サービスの AWS リージョン"),
            ),
            ToolParameter(
                name="task_type",
                label=I18nObject(en_US="Task Type", ja_JP="タスク種別"),
                type=ToolParameter.ToolParameterType.STRING,
                required=False,
                default="TEXT_IMAGE",
                form=ToolParameter.ToolParameterForm.LLM,
                human_description=I18nObject(en_US="Type of image generation task", ja_JP="画像生成タスクの種類"),
            ),
            ToolParameter(
                name="quality",
                label=I18nObject(en_US="Quality", ja_JP="品質"),
                type=ToolParameter.ToolParameterType.STRING,
                required=False,
                default="standard",
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US="Quality of the generated image (standard or premium)",
                    ja_JP="生成画像の品質（standard または premium）",
                ),
            ),
            ToolParameter(
                name="colors",
                label=I18nObject(en_US="Colors", ja_JP="カラー一覧"),
                type=ToolParameter.ToolParameterType.STRING,
                required=False,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US="List of colors for color-guided generation, example: #ff8080-#ffb280-#ffe680-#ffe680",
                    ja_JP="カラーガイド生成に使う色リスト。例: #ff8080-#ffb280-#ffe680-#ffe680",
                ),
            ),
            ToolParameter(
                name="similarity_strength",
                label=I18nObject(en_US="Similarity Strength", ja_JP="類似度の強さ"),
                type=ToolParameter.ToolParameterType.NUMBER,
                required=False,
                default=0.5,
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US="How similar the generated image should be to the input image (0.0 to 1.0)",
                    ja_JP="生成画像を入力画像にどの程度似せるか (0.0〜1.0)",
                ),
            ),
            ToolParameter(
                name="mask_prompt",
                label=I18nObject(en_US="Mask Prompt", ja_JP="マスク用プロンプト"),
                type=ToolParameter.ToolParameterType.STRING,
                required=False,
                form=ToolParameter.ToolParameterForm.LLM,
                human_description=I18nObject(
                    en_US="Text description to generate mask for inpainting/outpainting",
                    ja_JP="インペインティング/アウトペインティング用のマスクを生成するテキスト",
                ),
            ),
            ToolParameter(
                name="outpainting_mode",
                label=I18nObject(en_US="Outpainting Mode", ja_JP="アウトペインティングモード"),
                type=ToolParameter.ToolParameterType.STRING,
                required=False,
                default="DEFAULT",
                form=ToolParameter.ToolParameterForm.FORM,
                human_description=I18nObject(
                    en_US="Mode for outpainting (DEFAULT or other supported modes)",
                    ja_JP="アウトペインティングのモード（DEFAULT など）",
                ),
            ),
        ]

        return parameters
